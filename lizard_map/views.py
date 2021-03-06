try:
    import cStringIO as StringIO
except ImportError:
    import StringIO
import csv
import datetime
import logging
import re
import urllib2
from dateutil import parser as date_parser

from PIL import Image
from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.urlresolvers import reverse
from django.db import transaction
from django.http import HttpResponse
from django.http import HttpResponseBadRequest, HttpResponseNotFound
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.template import RequestContext
from django.template.loader import render_to_string
from django.utils import simplejson as json
from django.utils.translation import ugettext as _
from django.views.decorators.cache import never_cache
from django.views.generic.base import TemplateView
from django.views.generic.base import View
from django.views.generic.edit import FormView
from lizard_map.adapter import adapter_serialize
from lizard_ui.layout import Action
from lizard_ui.models import ApplicationIcon
from lizard_ui.views import IconView
from lizard_ui.views import UiView
from lizard_ui.views import ViewContextMixin
from rest_framework.response import Response as RestResponse
from rest_framework.views import APIView
import iso8601
import mapnik

from lizard_map import coordinates
from lizard_map.adapter import adapter_entrypoint
from lizard_map.adapter import adapter_layer_arguments
from lizard_map.adapter import parse_identifier_json
from lizard_map.coordinates import DEFAULT_OSM_LAYER_URL
from lizard_map.coordinates import transform_point
from lizard_map.dateperiods import ALL
from lizard_map.dateperiods import MONTH
from lizard_map.daterange import SESSION_DT_END
from lizard_map.daterange import SESSION_DT_RANGETYPE
from lizard_map.daterange import SESSION_DT_START
from lizard_map.daterange import current_start_end_dates
from lizard_map.forms import CollageAddForm
from lizard_map.forms import CollageForm
from lizard_map.forms import CollageItemEditorForm
from lizard_map.forms import EditForm
from lizard_map.forms import EmptyForm
from lizard_map.forms import WorkspaceLoadForm
from lizard_map.forms import WorkspaceSaveForm
from lizard_map.lizard_widgets import Legend
from lizard_map.models import BackgroundMap
from lizard_map.models import CollageEdit
from lizard_map.models import CollageEditItem
from lizard_map.models import Setting
from lizard_map.models import WorkspaceEdit
from lizard_map.models import WorkspaceEditItem
from lizard_map.models import WorkspaceStorage
from lizard_map.models import WorkspaceStorageItem
from lizard_map.utility import analyze_http_user_agent

CUSTOM_LEGENDS = 'custom_legends'
MAP_LOCATION = 'map_location'
MAP_BASE_LAYER = 'map_base_layer'  # The selected base layer
TIME_BETWEEN_VIDEO_POPUP = datetime.timedelta(days=1)
MAX_LOCATIONS = getattr(settings, 'MAX_LOCATIONS', 50)
# no way to know how the database driver escapes things, so apply
# a whitelist to strings, before passing them in the raw SQL query
LOCATION_NAME_CHARACTER_WHITELIST = re.compile(r'''[\W^ ^\,^\-^\.]''')

DEFAULT_START_EXTENT = '-14675, 6668977, 1254790, 6964942'
DEFAULT_PROJECTION = 'EPSG:900913'


logger = logging.getLogger(__name__)


class GoogleTrackingMixin(object):
    """
    Google tracking code.
    """
    def google_tracking_code(self):
        try:
            return settings.GOOGLE_TRACKING_CODE
        except AttributeError:
            return None


class WorkspaceMixin(object):
    """Add workspace and map variables.
    """
    # Override with an empty string for no click handler.
    javascript_click_handler = 'popup_click_handler'
    # javascript_click_handler = ''

    @property
    def workspace(self):
        """Implement a function that returns a workspace-storage,
        workspace-edit or other workspace."""
        pass

    def javascript_hover_handler(self):
        if not hasattr(self, '_javascript_hover_handler'):
            self._javascript_hover_handler = Setting.get(
                'javascript_hover_handler', None)
        return self._javascript_hover_handler

    def extra_wms_layers(self):
        """Overwrite to add custom WMS layers to your view.

        Every item should be a dict with five items. ``models.py`` puts the
        following in there for example::

               {'wms_id': workspace_item.id,
                'name': workspace_item.name,
                'url': adapter_layer.get('url', ''),
                'params': adapter_layer.get('params', '{}'),
                'options': adapter_layer.get('options', '{}'),
               }

        """
        return []

    def wms_layers(self):
        """Return the workspace's and our own extra wms layers."""
        from_workspace = self.workspace.wms_layers()
        extra = self.extra_wms_layers() or []
        # ^^^ To work around '[] + None' error.
        return from_workspace + extra


class WorkspaceEditMixin(WorkspaceMixin):
    def workspace_edit(self):
        """Return your workspace"""
        if not hasattr(self, '_workspace_edit'):
            self._workspace_edit = WorkspaceEdit.get_or_create(
                self.request.session.session_key, user=self.request.user)
        return self._workspace_edit

    @property
    def workspace(self):
        return self.workspace_edit()


class MapMixin(object):
    """All map stuff
    """
    # def maps(self):
    #     # Add map variables.
    #     self.map_variables = map_variables(self.request)
    #     return ""

    def max_extent(self):
        s = Setting.extent(
            'max_extent',
            '-20037508.34, -20037508.34, 20037508.34, 20037508.34')
        return s

    def start_extent(self):
        # Hack: we need to have a session right away for toggling ws items.
        self.request.session[
            'make_sure_session_is_initialized'] = 'hurray'
        # End of the hack.
        map_location = Setting.extent(
            'start_extent',
            DEFAULT_START_EXTENT)
        if MAP_LOCATION in self.request.session:
            map_location = self.request.session[MAP_LOCATION]
            logger.debug('Fetched map coordinates from session: '
                         '%s' % (map_location))
        return map_location

    def projection(self):
        return Setting.get('projection', DEFAULT_PROJECTION)

    def display_projection(self):
        return Setting.get('projection', 'EPSG:4326')

    def googlemaps_api_key(self):
        return Setting.get('projection', '')  # Must be defined

    def base_layer_name(self):
        if MAP_BASE_LAYER in self.request.session:
            return self.request.session[MAP_BASE_LAYER]
        return ""

    @property
    def backgrounds(self):
        if not hasattr(self, '_backgrounds'):
            self._backgrounds = BackgroundMap.objects.filter(active=True)
        return self._backgrounds

    def has_google(self):
        # For the client side to determine is there is a google map.
        if self.backgrounds.filter(
            layer_type=BackgroundMap.LAYER_TYPE_GOOGLE).count() > 0:
            return True
        return False

    def background_maps(self):
        if self.backgrounds:
            return self.backgrounds
        logger.warn("No background maps are active. Taking default.")
        return [BackgroundMap(
                    name='Default map',
                    default=True,
                    active=True,
                    layer_type=BackgroundMap.LAYER_TYPE_OSM,
                    layer_url=DEFAULT_OSM_LAYER_URL), ]


class CollageMixin(object):

    def collage_edit(self):
        if not hasattr(self, '_collage_edit'):
            self._collage_edit = CollageEdit.get_or_create(
                self.request.session.session_key, user=self.request.user)
        return self._collage_edit


class CrumbsMixin(object):
    def find_app_description(self, url):
        """An App doesn't generally know what it is called on the
        current site.  E.g., if the front page has an app called
        "Metingen" that links to "/fews/fewsjdbc/almere/", then the
        breadcrumb should show "home > metingen" instead of "home >
        fewsjdbc > almere", but the app doesn't know this.

        This function tries to help. It returns a tuple with three
        elements:
        - A list of breadcrumbs consisting of app screens (other than
          Home), the last item of which leads to this app
        - The part of the URL that led to this guess
        - The rest of the URL (that a view can use to build further
          parts of the breadcrumbs list)

        If there are multiple screens that appear to fit, we use the
        one that "uses up" most of the URL.
        """

        maxlength = None
        found = (None, None, None)

        if not url.startswith('/'):
            url = '/' + url

        for icon in ApplicationIcon.objects.all():
            iconurl = icon.url
            if iconurl.startswith('http://'):
                continue
            if not iconurl.startswith('/'):
                iconurl = '/' + iconurl

            if url.startswith(iconurl):
                if maxlength is not None and len(iconurl) < maxlength:
                    continue

                crumb = {'url': icon.url,
                         'description': icon.name,
                         'title': icon.description or icon.name}

                maxlength = len(iconurl)
                found = (icon.application_screen.crumbs() + [crumb],
                         iconurl,
                         url[len(iconurl):])

        return found

    def crumbs(self):
        """Returns a list of dictionaries, with keys
        'url', 'description' and 'title'. Views in Apps should
        override this function, use super() to get this default, and
        then add their own crumbs."""
        pass

        initial = [{
                'url': '/',
                'description': 'Home',
                'title': _('Back to homepage')}]

        toapp, self.url_to_app, self.url_after_app = (
            self.find_app_description(self.request.path))

        if toapp:
            return initial + toapp
        else:
            return initial


class AppView(WorkspaceEditMixin, GoogleTrackingMixin, CollageMixin,
              MapMixin, UiView):
    """Main map view (using twitter bootstrap)."""

    @property
    def show_secondary_sidebar_title(self):
        return _('Layers')

    show_secondary_sidebar_icon = 'icon-list'

    @property
    def show_rightbar_title(self):
        return _('Legend')

    @property
    def legends(self):
        """Return legends for the rightbar."""
        result = []
        workspace_items = self.workspace.workspace_items.filter(
            visible=True)
        for workspace_item in workspace_items:
            logger.debug("Looking for legend url for %s...", workspace_item)
            found_suitable_legend = False
            if not hasattr(workspace_item.adapter, 'legend'):
                logger.debug("No legend() on this ws item's adapter.")
            else:
                legend = workspace_item.adapter.legend()
                if legend:
                    result.append(
                        Legend(
                            name=workspace_item.name,
                            subitems=legend
                        )
                    )
                    found_suitable_legend = True
            if not found_suitable_legend:
                if not hasattr(workspace_item.adapter, 'legend_image_urls'):
                    logger.debug(
                        "No legend_image_urls() on this ws item's adapter.")
                else:
                    img_urls = workspace_item.adapter.legend_image_urls()
                    if img_urls:
                        result.append(
                            Legend(
                                name=workspace_item.name,
                                subitems=[{'img_url': img_url}
                                          for img_url in img_urls]
                            )
                        )
                        found_suitable_legend = True
        return result

    @property
    def content_actions(self):
        """Add default-location-zoom."""
        actions = super(AppView, self).content_actions
        if getattr(settings, 'MAP_SHOW_MULTISELECT', True):
            activate_multiselect = Action(
                name=_('Multi-select'),
                description=_('Select multiple items'),
                url="javascript:void(null)",
                icon='icon-star-empty',
                klass='map-multiple-selection')
            actions.insert(0, activate_multiselect)
        if getattr(settings, 'MAP_SHOW_DATE_RANGE', True):
            set_date_range = Action(
                name='',
                description=_('Change the date range of the measurements'),
                url='javascript:void(null)',
                icon='icon-calendar',
                klass='popup-date-range')
            actions.insert(0, set_date_range)
        if getattr(settings, 'MAP_SHOW_DEFAULT_ZOOM', True):
            zoom_to_default = Action(
                name=_('Default zoom'),
                description=_('Zoom to default location'),
                url=reverse('lizard_map.map_location_load_default'),
                icon='icon-screenshot',
                klass='map-load-default-location')
            actions.insert(0, zoom_to_default)
        return actions


MapView = AppView  # BBB


class WorkspaceStorageListView(
    UiView, GoogleTrackingMixin):
    """Show list of storage workspaces."""

    template_name = 'lizard_map/workspace_storage_list.html'

    def workspaces(self):
        return WorkspaceStorage.objects.all()


class WorkspaceStorageView(AppView):
    """Workspace storage view."""
    template_name = 'lizard_map/workspace_storage_detail.html'
    show_secondary_sidebar_title = False  # Don't show the 'layers' button.

    @property
    def workspace(self):
        """Return a workspace"""
        if not hasattr(self, '_workspace'):
            if self.workspace_id:
                self._workspace = WorkspaceStorage.objects.get(
                    pk=self.workspace_id)
            elif self.workspace_slug:
                self._workspace = WorkspaceStorage.objects.get(
                    secret_slug=self.workspace_slug)
            if hasattr(self, '_workspace'):
                # also copy extent to session again
                if self._workspace.extent_is_set:
                    try:
                        self.request.session[MAP_LOCATION] = {
                            'top': self._workspace.y_max,
                            'left': self._workspace.x_min,
                            'right': self._workspace.x_max,
                            'bottom': self._workspace.y_min
                        }
                    except:
                        logger.exception(
                            'Failed to load extent from workspace '
                            'storage. Skipping...')
        return self._workspace

    def get(self, request, *args, **kwargs):
        self.workspace_id = kwargs.get('workspace_id', None)
        self.workspace_slug = kwargs.get('workspace_storage_slug', None)
        return super(WorkspaceStorageView, self).get(
            request, *args, **kwargs)

    @property
    def page_title(self):
        return self.workspace.name

    @property
    def breadcrumbs(self):
        """Return homepage + ourselves as breadcrumbs."""
        result = [self.home_breadcrumb_element,
                  Action(name=self.workspace.name)]
        return result


class ActionDialogView(ViewContextMixin, FormView):
    """
    Generic Action Dialog View.

    Input from user is expected as form. Then an action is performed.
    """

    # Used with "GET" or "POST" with invalid form.
    template_name = 'lizard_map/form_workspace_save.html'
    # Used with "POST" with valid form.
    template_name_success = 'lizard_map/form_workspace_save_success.html'
    # Form which is added to the context for your templates.
    form_class = WorkspaceSaveForm  # Define your form

    success_url = './'

    def form_valid_action(self, form):
        """
        Implement your action here.

        Normally return None. If a not-None is returned, that will be
        returned as result.
        """
        pass

    def form_valid(self, form):
        """
        Return rendered template_name_success.
        """
        logger.debug("form is valid")
        result = self.form_valid_action(form)
        if result:
            return result
        return render(
            self.request,
            self.template_name_success,
            self.get_context_data())

    def form_invalid(self, form):
        """
        Return rendered template_name with current form with errors.
        """
        logger.debug("form is invalid")
        html = render_to_string(
            self.template_name, self.get_context_data(form=form),
            context_instance=RequestContext(self.request))
        return HttpResponseBadRequest(html)

    def get(self, request, *args, **kwargs):
        """Added request to initial, so in your form constructor you
        can use request."""
        self.initial.update({'request': request})
        return super(ActionDialogView, self).get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        """Added request to initial, so in your form constructor you
        can use request."""
        self.initial.update({'request': request})
        return super(ActionDialogView, self).post(request, *args, **kwargs)


class WorkspaceSaveView(ActionDialogView):
    template_name = 'lizard_map/form_workspace_save.html'
    template_name_success = 'lizard_map/form_workspace_save_success.html'
    template_name_forbidden = '403.html'
    form_class = WorkspaceSaveForm  # Define your form

    def form_valid_action(self, form):
        """
        Save edit workspace to storage workspace
        """
        logger.debug("Saving stuff...")
        form_data = form.cleaned_data
        # Should be get, else there is nothing to save...
        workspace_edit = WorkspaceEdit.get_or_create(
           self.request.session.session_key, self.request.user)
        # TODO: quota, warning on duplicate names.

        # For the initial release of Lizard 3, turn required authorization
        # OFF.
        user = self.request.user
        if not user.is_authenticated():
            user = None
            # html = render_to_string(
            #     self.template_name_forbidden,
            #     {'message': ('U kunt geen workspace opslaan als U '
            #                  'niet bent ingelogd.')},
            #     context_instance=RequestContext(self.request))
            # return HttpResponseForbidden(html)
        extent = None
        if (MAP_LOCATION in self.request.session
            and self.request.session[MAP_LOCATION]):
            extent = self.request.session[MAP_LOCATION]
        logger.debug("Before secret slug.")
        secret_slug = (workspace_edit.
                       save_to_storage(name=form_data['name'],
                                       owner=user,
                                       extent=extent))
        logger.debug("After secret slug. slug=%s" % (secret_slug,))

        self.saved_workspace_url = self.request.build_absolute_uri(
            reverse('lizard_map_workspace_slug_storage',
                    kwargs={'workspace_storage_slug': secret_slug}))


class WorkspaceLoadView(ActionDialogView):
    template_name = 'lizard_map/form_workspace_load.html'
    template_name_success = 'lizard_map/form_workspace_load_success.html'
    form_class = WorkspaceLoadForm  # Define your form

    def form_valid_action(self, form):
        """
        Load storage workspace to edit workspace
        """
        logger.debug("Loading stuff...")
        form_data = form.cleaned_data

        workspace_edit = WorkspaceEdit.get_or_create(
           self.request.session.session_key, self.request.user)
        # TODO: check permissions.
        workspace_storage = WorkspaceStorage.objects.get(pk=form_data['id'])
        workspace_edit.load_from_storage(workspace_storage)

        # also copy extent to session again
        if workspace_storage.extent_is_set:
            self.request.session[MAP_LOCATION] = {
                'top': workspace_storage.y_max,
                'left': workspace_storage.x_min,
                'right': workspace_storage.x_max,
                'bottom': workspace_storage.y_min
            }

        redirect = {
            "redirect": reverse("lizard_ui.icons")
        }
        return HttpResponse(json.dumps(redirect))


class CollageItemEditorView(ActionDialogView):
    """
    Popup per collage item for adjustments in the graph.

    TODO: make prettier. Split global settings and per collage-item settings.
    """
    template_name = 'lizard_map/box_collage_item_editor.html'
    template_name_success = 'lizard_map/box_collage_item_editor_success.html'
    form_class = CollageItemEditorForm

    def get(self, request, *args, **kwargs):
        self.collage_item_id = kwargs['collage_item_id']

        self.collage_item = CollageEditItem.objects.get(
            pk=self.collage_item_id)
        self.initial.update(self.collage_item.form_initial())
        return super(CollageItemEditorView, self).get(
            request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.collage_item_id = kwargs['collage_item_id']
        return super(CollageItemEditorView, self).post(
            request, *args, **kwargs)

    @transaction.commit_on_success
    def form_valid_action(self, form):
        """
        Change collage-item(s) accordingly

        This is a little tricky: some fields are for the graph itself,
        like title or y_min. Since the graph is used for multiple
        collage-items, all those items are updated.

        TODO: unit test.
        TODO: unit test.
        TODO: unit test.
        """

        # Fields that must be applied to all group items
        group_fields = {'title': None,
                        'y_min': None,
                        'y_max': None,
                        'x_label': None,
                        'y_label': None,
                        'aggregation_period': None,
                        'restrict_to_month': None}

        data = form.cleaned_data
        # The collage item being edited.
        collage_item = CollageEditItem.objects.get(pk=self.collage_item_id)

        # Select group to update the parameters.
        # Note: we also update invisible items
        grouped_collage_items, collage_item_group = group_collage_items(
            CollageEditItem.objects.filter(collage=collage_item.collage))

        grouping_hint = collage_item_group[collage_item.id]
        collage_items = grouped_collage_items[grouping_hint]

        # Loop all collage_items in group
        for single_collage_item in collage_items:
            # Model field for every collage item.
            single_collage_item.aggregation_period = data.get(
                'aggregation_period', ALL)

            identifier = single_collage_item.identifier
            new_layout = identifier.get('layout', {})

            # We have two instances of collage_item, only editing (and
            # saving) the one in the group list.
            if single_collage_item.id == collage_item.id:
                single_collage_item.boundary_value = data['boundary_value']
                single_collage_item.percentile_value = data['percentile_value']

            # Layout properties
            for k, v in data.items():
                # Check per field if it is a group field.
                if (single_collage_item.id == collage_item.id or
                    k in group_fields):

                    # Everything resulting in True must be saved
                    # 0.0 must be saved
                    # u'' must be deleted
                    # False must be deleted
                    if v or isinstance(v, float):
                        new_layout[k] = v
                    else:
                        if k in new_layout:
                            del new_layout[k]

            # Restrict to month option which is currently used in NHI only.
            if 'aggregation_period' in new_layout:
                if int(new_layout['aggregation_period']) != MONTH:
                    # Should not be there, but you never know.
                    if 'restrict_to_month' in new_layout:
                        del new_layout['restrict_to_month']
            if ('restrict_to_month' in new_layout and
                (new_layout['restrict_to_month'] == '0' or
                new_layout['restrict_to_month'] is None)):

                del new_layout['restrict_to_month']

            identifier['layout'] = new_layout
            single_collage_item.identifier = identifier
            single_collage_item.save()


# L3
@never_cache
def workspace_item_reorder(
    request, workspace_edit=None, workspace_items_order=None):
    """reorder workspace items.

    reorders workspace_item[] in new order.
    """
    if workspace_edit is None:
        workspace_edit = WorkspaceEdit.get_or_create(
            request.session.session_key, request.user)
    if workspace_items_order is None:
        workspace_items_order = dict([
                (workspace_item_id, index * 10) for
                index, workspace_item_id in enumerate(
                    request.POST.getlist('workspace-item[]'))])

    for workspace_item in workspace_edit.workspace_items.all():
        workspace_item.index = workspace_items_order.get(
            str(workspace_item.pk), 1000)
        print workspace_item.id, workspace_item.index
        workspace_item.save()
    return HttpResponse("")


# L3
@never_cache
def workspace_item_toggle(
    request,
    workspace_edit=None):

    """Toggle workspace item in workspace.

    This means: if the workspace-item is already in the workspace,
    remove it. If it is not in the workspace, add it.

    Return if it is added (True), or removed (False)
    """

    # For testing, workspace_edit can be given.
    if workspace_edit is None:
        workspace_edit = WorkspaceEdit.get_or_create(
            request.session.session_key, request.user)
    name = request.POST['name']
    adapter_class = request.POST['adapter_class']
    adapter_layer_json = request.POST['adapter_layer_json']

    added = workspace_edit.toggle_workspace_item(
        name, adapter_class, adapter_layer_json)

    return HttpResponse(json.dumps(added))


# L3
@never_cache
def workspace_edit_item(
    request, workspace_edit=None, workspace_item_id=None, visible=None):
    """Sets (in)visibility of a workspace_item

    workspace_edit is added for testing
    """
    if workspace_edit is None:
        workspace_edit = WorkspaceEdit.get_or_create(
            request.session.session_key, request.user)
    if workspace_item_id is None:
        workspace_item_id = request.POST['workspace_item_id']
    workspace_item = workspace_edit.workspace_items.get(
        pk=workspace_item_id)
    if visible is None:
        visible = request.POST.get('visible', None)
    if visible:
        lookup = {'true': True, 'false': False}
        workspace_item.visible = lookup[visible]
    workspace_item.save()

    return HttpResponse("")


# L3
@never_cache
def workspace_item_delete(request, workspace_edit=None, object_id=None):
    """delete workspace item from workspace

    returns true if >= 1 items were deleted

    if workspace_item_id is not provided, it tries to get the variable
    workspace_item_id from the request.POST
    """
    if object_id is None:
        object_id = request.POST['object_id']
    if workspace_edit is None:
        workspace_edit = WorkspaceEdit.get_or_create(
            request.session.session_key, request.user)
    workspace_items = workspace_edit.workspace_items.filter(pk=object_id)
    deleted = True if workspace_items.count() > 0 else False
    workspace_items.delete()

    return HttpResponse(json.dumps(deleted))


@never_cache
def workspace_item_extent(request, item_class=WorkspaceEditItem):
    """Return extent for the workspace item in json.

    Transform to correct client-side projection, then return coordinates.

    With ``item_class`` we can customize it in
    ``saved_workspace_item_extent()``.

    """

    workspace_item_id = request.GET['workspace_item_id']
    workspace_item = get_object_or_404(item_class, pk=workspace_item_id)
    extent = workspace_item.adapter.extent()
    peastnorth = transform_point(extent['east'], extent['north'],
                                 from_proj='google')
    pwestsouth = transform_point(extent['west'], extent['south'],
                                 from_proj='google')
    return HttpResponse(json.dumps({
                'east': peastnorth.get_x(),
                'north': peastnorth.get_y(),
                'west': pwestsouth.get_x(),
                'south': pwestsouth.get_y(),
                }))


@never_cache
def saved_workspace_item_extent(request):
    """Return extent for the *saved* workspace item in json.
    """
    return workspace_item_extent(request, item_class=WorkspaceStorageItem)


def popup_json(found, popup_id=None, hide_add_snippet=False, request=None):
    """Return html with info on list of 'found' objects.

    Optionally give pagenumber (starts at 0). If omitted, just join
    everything.

    found: list of dictionaries {'distance': ..., 'timeserie': ...,
    'workspace_item': ..., 'identifier': ..., ['grouping_hint'...]}.

    If 'grouping_hint' is given, that is used to group items,
    otherwise the workspace_item.id. This way a single workspace item
    can have things show up in different tabs. Please don't use
    grouping_hints that could possibly come from other workspace items
    (use the workspace item id in the hint).

    The maximum number of tabs in popups can be configured via
    POPUP_MAX_TABS in your site's settings.py. POPUP_MAX_TABS
    is optional and defaults to 3.

    Note: identifier must be a dict. {'id': the_real_id}.

    Result format (used by the javascript popup function):

    result = {'id': popup_id,
              'x': x_found,
              'y': y_found,
              'html': result_html,
              'big': big_popup,
              }
    """

    html = {}
    # x_found = None
    # y_found = None

    # Regroup found list of objects into workspace_items.
    display_groups = {}
    display_group_order = []
    for display_object in found:
        if 'grouping_hint' in display_object:
            key = display_object['grouping_hint']
        else:
            key = display_object['workspace_item'].id

        if key not in display_groups:
            display_groups[key] = []
        display_groups[key].append(display_object)
        if key not in display_group_order:
            display_group_order.append(key)

    if len(display_groups) > 1:
        big_popup = True
    else:
        big_popup = False

    # Now display them.
    for key, display_group in display_groups.items():
        # There MUST be at least one item in the group
        workspace_item = display_group[0]['workspace_item']

        add_snippet = True

        try:
            identifiers = [display_object['identifier']
                           for display_object in display_group]
        except:
            logger.critical(
                "No 'identifier' found in a display_object. Perhaps an "
                "incorrect shapefile?")
            identifiers = None
        if identifiers is None:
            continue

        # Passing the request object as a layout_option is a bit of a hack,
        # but for some use cases we really need access to it in the html()
        # method of a WorkspaceItemAdapter, which unfortunately is
        # lacking a **kwargs at this moment.

        html_per_workspace_item = workspace_item.adapter.html(
            identifiers=identifiers,
            layout_options={'add_snippet': add_snippet,
                            'legend': True,
                            'request': request},
            )

        # if 'google_coords' in display_object:
        #     x_found, y_found = display_object['google_coords']
        html[key] = html_per_workspace_item

    popup_max_tabs = Setting.get('popup_max_tabs', None)
    if popup_max_tabs is None:
        popup_max_tabs = getattr(settings, 'POPUP_MAX_TABS', 3)
    else:
        popup_max_tabs = int(popup_max_tabs)
    result_html = [html[key] for key in display_group_order][:popup_max_tabs]

    if popup_id is None:
        popup_id = 'popup-id'
    result = {'id': popup_id,
              # 'x': x_found,
              # 'y': y_found,
              'html': result_html,
              'big': big_popup,
              }
    return HttpResponse(json.dumps(result))


def group_collage_items(collage_items):
    """
    Group collage items.

    The grouping is done automagically by adapter property "grouping
    hint", or adapter/adapter_layer_arguments by creating collage
    items with extra property "identifiers".

    TODO: test
    """
    # for collage_item in collage_items:
    #     collage_item.identifiers = [collage_item.identifier, ]

    # Identifiers by grouping hint. Content is a list with collage
    # items.
    grouped_collage_items = {}
    collage_item_group = {}  # For each collage_item_id: which grouping_hint?

    for collage_item in collage_items:
        grouping_hint = collage_item.grouping_hint
        if grouping_hint not in grouped_collage_items:
            grouped_collage_items[grouping_hint] = []
        grouped_collage_items[grouping_hint].append(
            collage_item)
        collage_item_group[collage_item.id] = grouping_hint

    return grouped_collage_items, collage_item_group


# Updated for L3.
def popup_collage_json(collage_items, popup_id, request=None):
    """
    Display collage. Each item in a separate tab.
    """

    html = []
    big_popup = True

    grouped_collage_items, _ = group_collage_items(collage_items)
    for collage_items in grouped_collage_items.values():
        collage_item = collage_items[0]  # Each group always has items.
        identifiers = [collage_item.identifier for
                       collage_item in collage_items]

        html.append(
            collage_item.html(identifiers=identifiers, is_collage=True,
                              request=request))

    result = {'id': popup_id,
              'html': html,
              'big': big_popup,
              }
    return HttpResponse(json.dumps(result))


# Collages stuff


# L3.
@never_cache
def collage_popup(request,
                  collage_id=None,
                  collage_item_id=None,
                  template='lizard_map/collage.html'):
    """Render page with collage item(s) in popup format
    """
    collage = CollageEdit.get_or_create(
        request.session.session_key, request.user)
    popup_id = 'popup-collage'

    collage_items = collage.collage_items.filter(visible=True)

    # This item is filled when clicking on a single collage item.
    if collage_item_id is not None:
        collage_items = collage_items.filter(pk=collage_item_id)

    # Only one collage popup allowed, also check jquery.workspace.js
    return popup_collage_json(
        collage_items,
        popup_id=popup_id,
        request=request)


# TODO: Update to L3
def legend_edit(request):
    """Updates a session legend.

    POST parameters:
    name
    min_value (optional)
    max_value (optional)
    steps (optional)
    min_color (optional): format ...
    max_color (optional)
    too_low_color (optional)
    too_high_color (optional)

    request['session']['custom_legends'][<name>] = {..}
    """

    # Get new legend from post parameters.
    options = ['min_value', 'max_value', 'steps',
               'min_color', 'max_color', 'too_low_color',
               'too_high_color']

    name = request.POST['name']
    new_legend = {}
    for option in options:
        value = request.POST.get(option, None)
        if value:
            new_legend[option] = value

    # Update session data with new obtained legend.
    custom_legends = request.session.get(CUSTOM_LEGENDS, {})
    custom_legends[name] = new_legend

    request.session[CUSTOM_LEGENDS] = custom_legends

    return HttpResponse('')


"""
Map stuff
"""


def wms(request, workspace_item_id, workspace_storage_id=None,
        workspace_storage_slug=None):
    """Return PNG as WMS service for given workspace_edit or
    workspace_storage.

    if workspace_storage_id and workspace_storage_slug are both not
    provided, it will take your own WorkspaceEdit.
    """

    workspace_item_id = int(workspace_item_id)

    if workspace_storage_id is not None:
        workspace_storage_id = int(workspace_storage_id)
        workspace = get_object_or_404(
            WorkspaceStorage, pk=workspace_storage_id)
    elif workspace_storage_slug is not None:
        workspace = get_object_or_404(
            WorkspaceStorage, secret_slug=workspace_storage_slug)
    else:
        workspace = WorkspaceEdit.get_or_create(
            request.session.session_key, request.user)

    # WMS standard parameters
    width = int(request.GET.get('WIDTH'))
    height = int(request.GET.get('HEIGHT'))
    req_layers = request.GET.get('LAYERS')
    req_layers = [layer.strip() for layer in req_layers.split(',')]
    bbox = request.GET.get('BBOX')
    bbox = tuple([float(i.strip()) for i in bbox.split(',')])
    srs = request.GET.get('SRS')
    # TODO: check that they're not none

    # Map settings
    mapnik_map = mapnik.Map(width, height)
    # Setup mapnik srs.
    mapnik_map.srs = coordinates.srs_to_mapnik_projection[srs]
    mapnik_map.background = mapnik.Color('transparent')
    #m.background = mapnik.Color('blue')

    workspace_items = workspace.workspace_items.filter(
        visible=True, id=workspace_item_id).reverse()
    # len(workspace_items) should be 1:
    # we no longer combine all generated layers into a single WMS layer
    for workspace_item in workspace_items:
        logger.debug("Drawing layer for %s..." % workspace_item)
        try:
            layers, styles = workspace_item.adapter.layer(
                layer_ids=req_layers,
                request=request)
            layers.reverse()  # first item should be drawn on top (=last)
            for layer in layers:
                mapnik_map.layers.append(layer)
            for name in styles:
                mapnik_map.append_style(name, styles[name])
        except:
            # This part may NEVER crash. Layers from workspace items
            # should prevent crashing themselves, but you never know.
            logger.exception("Error in drawing layer for %s" % workspace_item)

    #Zoom and create image
    logger.debug("Zooming to box...")
    mapnik_map.zoom_to_box(mapnik.Envelope(*bbox))
    # mapnik_map.zoom_to_box(layer.envelope())
    # just have mapnik render the png, as it should be faster
    # unfortunately mapnik doesn't support rendering to a stream (yet?)
    img = mapnik.Image(width, height)
    logger.debug("Rendering map...")
    mapnik.render(mapnik_map, img)
    response = HttpResponse(img.tostring('png'), content_type='image/png')
    return response


def search(workspace, google_x, google_y, radius):
    """Search workspace for given coordinates.

    Return a list of found results in "adapter.search" dictionary
    format.
    """
    found = []

    for workspace_item in workspace.workspace_items.filter(
        visible=True):

        try:
            search_results = workspace_item.adapter.search(
                google_x, google_y, radius=radius)
            found += search_results
        except:
            logger.exception(
                "Crashed while calling search on %s" %
                workspace_item)
    return found


# L3
def search_coordinates(request,
                       workspace_storage_id=None,
                       workspace_storage_slug=None,
                       _format='popup'):
    """searches for objects near GET x,y,radius returns json_popup of
    results.

    GET parameter user_workspace_id: a workspace-edit that is
    currently shown.

    Optional GET parameter srs, if omitted, assume google.

    Format: 'popup', 'name'
    """

    # workspace_manager = WorkspaceManager(request)
    # workspace_collections = workspace_manager.load_or_create()

    # xy params from the GET request.
    x = float(request.GET.get('x'))
    y = float(request.GET.get('y'))
    format = request.GET.get('format', _format)

    # TODO: convert radius to correct scale (works now for google + rd)
    radius = float(request.GET.get('radius'))
    radius_search = radius

    if 'HTTP_USER_AGENT' in request.META:
        analyzed_user_agent = analyze_http_user_agent(
            request.META['HTTP_USER_AGENT'])
        # It's more difficult to point with your finger than with the mouse.
        if analyzed_user_agent['device'] == 'iPad':
            radius_search = radius_search * 3

    srs = request.GET.get('srs')
    google_x, google_y = coordinates.srs_to_google(srs, x, y)

    # Load correct workspace, several possibilities
    if workspace_storage_id is not None:
        workspace = WorkspaceStorage.objects.get(pk=workspace_storage_id)
    elif workspace_storage_slug is not None:
        workspace = WorkspaceStorage.objects.get(
            secret_slug=workspace_storage_slug)
    else:
        user_workspace_id = request.GET.get('user_workspace_id', None)
        if user_workspace_id is not None:
            workspace = WorkspaceEdit.objects.get(pk=user_workspace_id)
        else:
            stored_workspace_id = request.GET.get('stored_workspace_id', None)
            workspace = WorkspaceStorage.objects.get(pk=stored_workspace_id)

    # The actual search!
    found = search(workspace, google_x, google_y, radius)
    logger.debug('>>> FOUND <<< %s\n%s' % (format, repr(found)))

    if found:
        # ``found`` is a list of dicts {'distance': ..., 'timeserie': ...}.
        found.sort(key=lambda item: item['distance'])
        if format == 'name':
            result = {}
            result['name'] = found[0]['name']
            # x, y = coordinates.google_to_srs(google_x, google_y, srs)
            # result['x'] = x
            # result['y'] = y

            # For the x/y we use the original x/y value to position
            # the popup to the lower right of the cursor to prevent
            # click propagation problems.
            result['x'] = x + (radius / 10)
            result['y'] = y - (radius / 10)
            return HttpResponse(json.dumps(result))
        elif format == 'object':
            result = [{'id':f['identifier'], 'name':f['name']}
            for f in found]

            return HttpResponse(json.dumps(result))

        else:
            # default: as popup
            return popup_json(found, request=request)
    else:
        if format == 'object':
            return HttpResponse([])
        else:
            return popup_json([], request=request)


class CollageDetailView(CollageMixin, UiView):
    """
    Shows "my collage" as big page.
    """
    title = _('Collage')
    template_name = 'lizard_map/collage_edit_detail.html'
    hide_statistics = False

    @property
    def content_actions(self):
        actions = super(CollageDetailView, self).content_actions
        if getattr(settings, 'MAP_SHOW_DATE_RANGE', True):
            set_date_range = Action(
                name='',
                description=_('Verander het datumbereik van de metingen.'),
                url='javascript:void(null)',
                icon='icon-calendar',
                klass='popup-date-range reload-after-action')
            actions.insert(0, set_date_range)
        return actions

    def breadcrumbs(self):
        initial = [
            Action(
                name=_('Home'),
                url='/',
                description=_('Back to homepage')
            ),
            Action(
                name=_('Collage'),
                url=reverse('lizard_map_collage_edit_detail')
            ),
        ]
        return initial

    def grouped_collage_items(self):
        """A grouped collage item is a collage item with property
        "identifiers": a list of identifiers """
        collage_items, _ = group_collage_items(
            self.collage_edit().collage_items.filter(visible=True))

        return collage_items

    def get(self, request, *args, **kwargs):
        self.hide_statistics = request.GET.get('hide_statistics', False)
        return super(CollageDetailView, self).get(request, *args, **kwargs)


class CollageStatisticsView(UiView):
    """
    Shows statistics for collage items.
    """
    template_name = 'lizard_map/box_statistics.html'
    collage_items = None

    def get(self, request, *args, **kwargs):
        collage_item_ids = request.GET.getlist('collage_item_id')
        self.collage_items = CollageEditItem.objects.filter(
            id__in=collage_item_ids)

        return super(CollageStatisticsView, self).get(request, *args, **kwargs)


class CollageView(CollageMixin, ActionDialogView):
    """
    Add collage item by coordinates
    """
    template_name = 'lizard_map/box_collage.html'
    template_name_success = template_name
    form_class = CollageForm

    def form_valid_action(self, form):
        """Find collage items and save them.
        """
        form_data = form.cleaned_data

        # Add items to collage by coordinates.
        x = float(form_data['x'])
        y = float(form_data['y'])
        # TODO: convert radius to correct scale (works now for google + rd)
        radius = float(form_data['radius'])
        workspace_id = form_data['workspace_id']
        srs = form_data['srs']
        google_x, google_y = coordinates.srs_to_google(srs, x, y)

        # Workspace.
        if form_data['workspace_type'] == 'workspace_storage':
            workspace = WorkspaceStorage.objects.get(pk=workspace_id)
        else:
            workspace = WorkspaceEdit.objects.get(pk=workspace_id)

        collage = CollageEdit.get_or_create(
            self.request.session.session_key, self.request.user)

        found = search(workspace, google_x, google_y, radius)

        for found_item in found:
            # Add all found items to collage.
            logger.debug("Adding collage item %s" % found_item['name'])
            #print '%r' % found_item['identifier']

            # Don't add them if they already exist
            adapter_class = found_item['workspace_item'].adapter_class
            adapter_layer_json = (found_item['workspace_item']
                                  .adapter_layer_json)
            name = found_item['name'][:80]
            identifier = found_item['identifier']

            if not collage.data_in_collage(adapter_class, adapter_layer_json,
                                           name, identifier):
                collage.collage_items.create(
                    adapter_class=adapter_class,
                    adapter_layer_json=adapter_layer_json,
                    name=name,
                    identifier=identifier)

        if not found:
            # Nothing found on this coordinates, return a 404
            return HttpResponseNotFound()


class CollageAddView(CollageMixin, ActionDialogView):
    """
    Add collage item by name + adapter_class + adapter_layer_json + identifier.
    """
    template_name = 'lizard_map/box_collage.html'
    template_name_success = template_name
    form_class = CollageAddForm

    def form_valid_action(self, form):
        data = form.cleaned_data
        collage = CollageEdit.get_or_create(
            self.request.session.session_key, self.request.user)

        # Parse_identifier_json is unnecessary, but it provides an
        # extra check.
        adapter_class = data['adapter_class']
        adapter_layer_json = data['adapter_layer_json']
        name = data['name'][:80]
        identifier = parse_identifier_json(data['identifier'])

        if collage.data_in_collage(adapter_class,
                                   adapter_layer_json,
                                   name,
                                   identifier):
            return

        collage.collage_items.create(
            adapter_class=adapter_class,
            adapter_layer_json=adapter_layer_json,
            name=name,
            identifier=identifier)


class CollageEmptyView(CollageView):
    """
    Empty collage.
    """
    form_class = EmptyForm

    def form_valid_action(self, form):
        """Delete all collage items from collage_edit.
        """
        logger.debug('Deleting all collage items from collage_edit')
        collage_edit = CollageEdit.get_or_create(
            self.request.session.session_key, self.request.user)
        collage_edit.collage_items.all().delete()


class CollageItemEditView(CollageView):
    """Edit collage item: create, (read, )update or delete"""
    form_class = EditForm

    def form_valid_action(self, form):
        form_data = form.cleaned_data
        collage_edit = CollageEdit.get_or_create(
            self.request.session.session_key, self.request.user)
        collage_item = collage_edit.collage_items.get(
            pk=form_data['object_id'])
        if form_data['action'] == 'update':
            logger.debug("Updating collage item...")
            collage_item.visible = form_data['visible']
            collage_item.save()
        elif form_data['action'] == 'delete':
            logger.debug("Deleting collage item...")
            collage_item.delete()


class CollagePopupView(CollageMixin, TemplateView):
    template_name = 'lizard_map/box_collage_popup.html'


class WorkspaceEmptyView(WorkspaceEditMixin, ActionDialogView):
    template_name = 'lizard_map/box_workspace.html'
    template_name_success = template_name
    form_class = EmptyForm

    def form_valid_action(self, form):
        """
        """
        workspace_edit = WorkspaceEdit.get_or_create(
            self.request.session.session_key, self.request.user)
        workspace_edit.workspace_items.all().delete()

"""
Map locations are stored in the session with key MAP_SESSION. It
contains a dictionary with fields x, y and zoom.
"""


def map_location_save(request):
    """
    Save map layout in session.

    - extent as strings (POST top, left, right, bottom).
    - selected base layer name.


    """
    top = request.POST['top']
    left = request.POST['left']
    right = request.POST['right']
    bottom = request.POST['bottom']
    base_layer_name = request.POST['base_layer_name']
    request.session[MAP_LOCATION] = {
        'top': top,
        'left': left,
        'right': right,
        'bottom': bottom}
    request.session[MAP_BASE_LAYER] = base_layer_name
    return HttpResponse("")


def map_location_load_default(request):
    """
    Return start_extent
    """
    extent = Setting.extent(
        'start_extent',
        DEFAULT_START_EXTENT)

    map_location = {'extent': extent}

    request.session[MAP_BASE_LAYER] = ''  # Reset selected base layer.

    return HttpResponse(json.dumps(map_location))


# Save map
def save_map_as_image(request):
    """
    Return map as png image to download.
    """
    data = {
        'width': int(request.GET.get('WIDTH')),
        'height': int(request.GET.get('HEIGHT')),
        'layers': [layer.strip()
                   for layer in request.GET.get('LAYERS').split(',')],
        'bbox': tuple([float(i.strip())
                       for i in request.GET.get('BBOX').split(',')]),
        'srs': request.GET.get('SRS'),
        'workspaces': request.session.get('workspaces'),
        'color': "transparent",
        'format': "png",
        'content_type': "application/x-png",
        }

    img = create_mapnik_image(request, data)
    buf = mapnik_image_to_stream(request, data, img)

    response = HttpResponse(buf.read())
    response['Content-Type'] = 'application/x-png'
    response['Content-disposition'] = 'Attachment; filename=%s' % 'kaart.png'

    return response


def create_mapnik_image(request, data):
    """TODO: remove copy-pasting.
    """
    # Map settings
    mapnik_map = mapnik.Map(data['width'], data['height'])
    layers = data['layers']
    # Setup mapnik srs.
    mapnik_map.srs = coordinates.srs_to_mapnik_projection[data['srs']]
    mapnik_map.background = mapnik.Color(data['color'])
    #m.background = mapnik.Color(data['color')]

    workspace = WorkspaceEdit.get_or_create(
        request.session.session_key, user=request.user)

    workspace_items = workspace.workspace_items.filter(
        visible=True).reverse()

    for workspace_item in workspace_items:
        logger.debug("Drawing layer for %s..." % workspace_item)
        layers, styles = workspace_item.adapter.layer(layer_ids=layers,
                                                      request=request)
        layers.reverse()  # first item should be drawn on top (=last)
        for layer in layers:
            mapnik_map.layers.append(layer)
        for name in styles:
            mapnik_map.append_style(name, styles[name])

    #Zoom and create image
    logger.debug("Zooming to box...")
    mapnik_map.zoom_to_box(mapnik.Envelope(*data['bbox']))
    img = mapnik.Image(data['width'], data['height'])
    logger.debug("Rendering map...")
    mapnik.render(mapnik_map, img)

    return img


def mapnik_image_to_stream(request, data, img):
    """
    Convert mapnik image object to bytes stream

    TODO: remove hardcoding url
    """
    http_user_agent = request.META.get('HTTP_USER_AGENT', '')
    logger.debug("Converting image to rgba...")

    bbox = ",".join([str(x) for x in data['bbox']])
    geoserver_img = urllib2.urlopen(
        "http://10.100.130.132:8080/geoserver/" +
        "wms?LAYERS=waterkaart&FORMAT=image%2Fpng&MAXRESOLUTION=364&SERVICE" +
        "=WMS&VERSION=1.1.1&REQUEST=GetMap&STYLES=" +
        "&EXCEPTIONS=application%2Fvnd." +
        "ogc.se_inimage&SRS=EPSG%3A28992&BBOX=" + str(bbox) +
        "&WIDTH=" + str(data['width']) +
        "&HEIGHT=" + str(data['height'])).read()
    # ^^^ TODO: This should be configurable! (Added by gnijholt on Sep
    # 28 2011)
    base_image = Image.open(StringIO.StringIO(geoserver_img))
    rgba_image = Image.fromstring('RGBA',
                                  (data['width'], data['height']),
                                  img.tostring()).convert("RGBA")

    base_w, base_h = base_image.size
    rgba_w, rgba_h = rgba_image.size

    offset = ((base_w - rgba_w), (base_h - rgba_h))
    base_image.paste(rgba_image, offset, rgba_image)
    # ^^^ Passing rgba_image twice to get transparency working in paste()

    buf = StringIO.StringIO()
    if 'MSIE 6.0' in http_user_agent:
        imgPIL = base_image.convert('P')
        imgPIL.save(buf, data['format'], transparency=0)
    else:
        base_image.save(buf, data['format'])
    buf.seek(0)
    return buf


# Statistics

def statistics_csv(request):
    """
    Return csv for statistics of given collage_items.

    Collage items are from your collage edit.

    TODO: when statistics must be downloaded from other collage-items,
    we must pass the object in a different way.

    TODO: make prettier

    TODO: this depends on the deprecated daterange.py. Erik-Jan knows most
    about that.

    """
    start_date, end_date = current_start_end_dates(request)
    collage = CollageEdit.get_or_create(
        request.session.session_key, request.user)
    collage_items = collage.collage_items.filter(visible=True)
    statistics = []
    for collage_item in collage_items:
        statistics.extend(collage_item.statistics(start_date, end_date))

    # Statistics as csv.
    filename = 'statistieken.csv'

    # Make the csv output.
    response = HttpResponse(mimetype='text/csv')
    response['Content-Disposition'] = ('attachment; filename="%s"' % filename)
    writer = csv.writer(response)
    writer.writerow(['Naam', 'Periode', 'Minimum', 'Maximum', 'Gemiddeld',
                     'Percentiel grens', 'Percentiel waarde',
                     'Grenswaarde', 'Aantal boven grenswaarde',
                     'Aantal onder grenswaarde'])
    for row in statistics:
        writer.writerow([
                row['name'], row['period'], row['min'], row['max'], row['avg'],
                row['percentile_value'], row['percentile'],
                row['boundary_value'], row['count_lt'], row['count_gte']])
    return response


# Adapter related views

class AdapterMixin(object):
    """
    Provide functions to get adapter stuff from get request

    Supported URL GET parameters: adapter_layer_json, identifier
    (multiple allowed), dt_start, dt_end.

    TODO: tests
    """
    def adapter(self, adapter_class):
        """
        named url arguments become kwargs
        """
        adapter_layer_json = self.request.GET.get("adapter_layer_json")
        layer_arguments = adapter_layer_arguments(adapter_layer_json)
        return adapter_entrypoint(adapter_class, layer_arguments)

    def identifiers(self):
        identifier_json_list = self.request.GET.getlist('identifier')
        identifier_list = [json.loads(identifier_json) for identifier_json in
                           identifier_json_list]
        return identifier_list

    def identifier(self):
        identifier_json = self.request.GET.get('identifier')
        identifier = parse_identifier_json(identifier_json)
        return identifier

    def start_end_dates_from_request(self):
        """
        Try to get dt_start, dt_end from url parameters, revert to
        "current_start_end_dates".

        dt_start and dt_end are in iso8601 format
        """
        current_start_date, current_end_date = current_start_end_dates(
                self.request)

        start_date_str = self.request.GET.get('dt_start', None)
        if start_date_str is None:
            start_date = current_start_date
        else:
            start_date = iso8601.parse_date(start_date_str)

        end_date_str = self.request.GET.get('dt_end', None)
        if end_date_str is None:
            end_date = current_end_date
        else:
            end_date = iso8601.parse_date(end_date_str)

        return start_date, end_date

    def layout_extra_from_request(self):
        """Check for url parameter layout_extra (in json) and return parsed.
        """
        result = {}
        layout_extra_json = self.request.GET.get('layout_extra', None)
        if layout_extra_json is not None:
            result = json.loads(layout_extra_json)
        return result


class ImageMixin(object):
    """
    Provide functions for a View that return an image.

    Supported URL GET parameters: width, height.

    TODO: tests
    """
    def width_height(self):
        width = self.request.GET.get('width', None)
        height = self.request.GET.get('height', None)

        if width is not None:
            width = int(width)

        if height is not None:
            height = int(height)

        return width, height


class AdapterImageView(AdapterMixin, ImageMixin, View):
    """
    Return result of adapter.image, using given parameters.

    URL GET parameters:
    - adapter_class (required)
    - identifier (required, multiple supported)
    - width, height (optional)
    - start_date, end_date (optional, iso8601 format, default current)
    - layout_extra (optional)
    """

    def get(self, request, *args, **kwargs):
        """
        Note: named url arguments become kwargs.
        """
        current_adapter = self.adapter(kwargs['adapter_class'])
        identifier_list = self.identifiers()
        width, height = self.width_height()

        start_date, end_date = self.start_end_dates_from_request()

        # Add animation slider position, info from session data.
        layout_extra = self.layout_extra_from_request()

        return current_adapter.image(
            identifier_list, start_date, end_date,
            width, height,
            layout_extra=layout_extra)


class AdapterValuesView(AdapterMixin, UiView):
    """
    Return values for a single identifier in csv or html.

    URL GET parameters:
    - adapter_class (required)
    - identifier (required, single)
    - output_type (optional, choices are 'csv' or 'html'. Default 'html')
    - start_date, end_date (optional, iso8601 format, default current)

    """
    template_name = 'lizard_map/box_table.html'

    def get(self, request, *args, **kwargs):
        adapter = self.adapter(kwargs['adapter_class'])
        output_type = self.kwargs.get('output_type', None)
        identifier = self.identifier()
        start_date, end_date = self.start_end_dates_from_request()

        self.values = adapter.values(identifier, start_date, end_date)

        self.name = adapter.location(**identifier).get('name', 'export')

        if output_type == 'csv':
            filename = ('%s.csv' % (self.name)).encode('us-ascii',
                                                       errors='ignore')
            # Make the csv output.
            response = HttpResponse(mimetype='text/csv')
            response['Content-Disposition'] = (
                'attachment; filename="%s"' %
                filename)
            writer = csv.writer(response)
            writer.writerow(['Datum + tijdstip', 'Waarde', 'Eenheid'])
            for row in self.values:
                writer.writerow([row['datetime'], row['value'], row['unit']])
            return response
        else:
            # Make html table using self.values
            return super(AdapterValuesView, self).get(
                request, *args, **kwargs)


class HomepageView(AppView, IconView):
    template_name = 'lizard_map/icons.html'


MapIconView = HomepageView  # BBB

#
# new RESTful Lizard API
#


class AdapterFlotGraphDataView(AdapterMixin, APIView):
    """
    Return result of adapter.flot_graph_data, using given parameters.

    URL GET parameters:
    - adapter_class (required)
    - identifier (required, multiple supported)
    - start_date, end_date (optional, iso8601 format, default current)
    - layout_extra (optional)
    """

    @never_cache
    def get(self, request, *args, **kwargs):
        """
        Note: named url arguments become kwargs.
        """
        current_adapter = self.adapter(kwargs['adapter_class'])
        identifier_list = self.identifiers()

        start_date, end_date = self.start_end_dates_from_request()

        # Add animation slider position, info from session data.
        layout_extra = self.layout_extra_from_request()

        result = current_adapter.flot_graph_data(
            identifier_list, start_date, end_date,
            layout_extra=layout_extra)
        return RestResponse(result)


# TODO: move this one over to a new fields.py.
class JsonDateTimeField(forms.DateTimeField):
    '''
    Supports field value as ISO 8601 string.
    '''
    def to_python(self, value):
        try:
            value = super(JsonDateTimeField, self).to_python(value)
        except ValidationError as parent_exception:
            try:
                value = date_parser.parse(value)
            except ValueError:
                raise parent_exception
        return value


# TODO: move this one over to forms.py.
class ViewStateForm(forms.Form):
    range_type = forms.CharField(
        required=False,
        help_text='custom, day, week, year, et cetera')
    dt_start = JsonDateTimeField(
        required=False,
        help_text='ISO8601 datetime string')
    dt_end = JsonDateTimeField(
        required=False,
        help_text='ISO8601 datetime string')


class ViewStateService(APIView, WorkspaceEditMixin):
    form = ViewStateForm

    @never_cache
    def get(self, request, *args, **kwargs):
        session = request.session

        # try getting values from session
        range_type = session.get(SESSION_DT_RANGETYPE, None)
        dt_start = session.get(SESSION_DT_START, None)
        dt_end = session.get(SESSION_DT_END, None)

        if not range_type:
            range_type = getattr(settings, 'DEFAULT_RANGE_TYPE', '2_day')
        elif range_type == 'custom' and not (dt_start and dt_end):
            range_type = getattr(settings, 'DEFAULT_RANGE_TYPE', '2_day')

        return RestResponse({
            'range_type': range_type,
            'dt_start': dt_start,
            'dt_end': dt_end
            })

    def put(self, request, *args, **kwargs):
        session = request.session

        # request.DATA contains the validated values
        # it will raise an error 400 exception upon first access
        # TODO adjust to restframework 2.x
        range_type = request.DATA['range_type']
        dt_start = request.DATA['dt_start']
        dt_end = request.DATA['dt_end']
        session[SESSION_DT_RANGETYPE] = range_type
        session[SESSION_DT_START] = dt_start
        session[SESSION_DT_END] = dt_end
        # also store in database: why in two places?
        if dt_start and dt_end:
            workspace_edit = self.workspace_edit()
            workspace_edit.dt_start = dt_start
            workspace_edit.dt_end = dt_end
            workspace_edit.save()
        return RestResponse()


class LocationListService(APIView, WorkspaceEditMixin):

    @never_cache
    def get(self, request, *args, **kwargs):
        name = request.GET.get('name', None)
        # clean weird character from the name
        name = name.strip()
        name = LOCATION_NAME_CHARACTER_WHITELIST.sub('', name)
        # grab this users workspace
        workspace_edit = self.workspace_edit()
        locations = []
        # iterate trough all selected layers
        for workspace_item in workspace_edit.workspace_items.all():
            # skip invisible items
            if not workspace_item.visible:
                continue
            adapter = workspace_item.adapter
            adapter_class = adapter.adapter_class
            layer_arguments = json.dumps(adapter.layer_arguments)
            # Skip items whose workspace adapter don't support location
            # searching.
            if not hasattr(adapter, 'location_list'):
                logger.debug("No location_list() on this ws item's adapter.")
                continue
            # request the list of locations from the adapter
            for (identifier,
                 collage_name,
                 location_name) in adapter.location_list(name):
                identifier = adapter_serialize(identifier)
                locations.append((adapter_class,
                                  layer_arguments,
                                  identifier,
                                  collage_name,
                                  location_name))
            # We can stop searching the remaining adapters in case
            # MAX_LOCATIONS is already reached.
            if len(locations) > MAX_LOCATIONS:
                break
        # ensure we don't return more than MAX_LOCATIONS values
        locations = locations[:MAX_LOCATIONS]
        add_href = reverse('lizard_map_collage_add')
        locations = [loc + (add_href,) for loc in locations]
        return RestResponse(locations)
