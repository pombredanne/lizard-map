import StringIO
import simplejson

import mapnik
import PIL.Image
from django.core.urlresolvers import reverse
from django.db.models import Max
from django.http import HttpResponse
from django.template import RequestContext
from django.shortcuts import get_object_or_404
from django.shortcuts import render_to_response
from django.views.decorators.cache import never_cache
import simplejson as json

from lizard_map import coordinates
from lizard_map.models import Workspace
from lizard_map.models import WorkspaceItem
from lizard_map.models import WorkspaceCollageSnippet

"""
Misc
"""
SCREEN_DPI = 72.0

def _inches_from_pixels(pixels):
    """Return size in inches for matplotlib's benefit"""
    return pixels / SCREEN_DPI

"""
Workspace stuff
"""

def workspace(request,
              workspace_id,
              template='lizard_map/workspace.html'):
    """Render page with one workspace"""
    workspace = get_object_or_404(Workspace, pk=workspace_id)
    return render_to_response(
        template,
        {'workspaces': {'user': [workspace]}},
        context_instance=RequestContext(request))


@never_cache
def workspace_item_reorder(request,
                           workspace_id=None,
                           template='lizard_map/tag_workspace.html'):
    """reorder workspace items. returns workspace_id

    reorders workspace_item[] in new order. expects all workspace_items from
    workspace

    TODO: check permissions
    """
    if workspace_id is None:
        workspace_id = request.GET['workspace_id']        

    workspace = get_object_or_404(Workspace, pk=workspace_id)
    workspace_items = [
        get_object_or_404(WorkspaceItem, pk=workspace_item_id) for
        workspace_item_id in request.POST.getlist('workspace_items[]')]
    for i, workspace_item in enumerate(workspace_items):
        workspace_item.workspace = workspace
        workspace_item.index = i * 10
        workspace_item.save()
    return HttpResponse(json.dumps(workspace.id))


# TODO: put item_add and item_edit in 1 function
@never_cache
def workspace_item_add(request,
                       workspace_id=None,
                       is_temp_workspace=False,
                       template='lizard_map/tag_workspace.html'):
    """add new workspace item to workspace. returns rendered workspace"""
    if workspace_id is None:
        workspace_id = request.POST['workspace_id']        
    workspace = get_object_or_404(Workspace, pk=workspace_id)
    name = request.POST['name']
    adapter_class = request.POST['adapter_class']
    adapter_layer_json = request.POST['adapter_layer_json']

    if is_temp_workspace:
        # only one workspace item is used in the temp workspace
        workspace.workspace_items.all().delete()
    if workspace.workspace_items.count() > 0:
        max_index = workspace.workspace_items.aggregate(
            Max('index'))['index__max']
    else:
        max_index = 10

    workspace.workspace_items.create(adapter_class=adapter_class,
                                     index=max_index + 10,
                                     adapter_layer_json=adapter_layer_json,
                                     name=name)
    return HttpResponse(json.dumps(workspace.id))


@never_cache
def workspace_item_edit(request, workspace_item_id=None, visible=None):
    """edits a workspace_item

    returns workspace_id

    TODO: permission
    """
    if workspace_item_id is None:
        workspace_item_id = request.POST['workspace_item_id']
    workspace_item = get_object_or_404(WorkspaceItem, pk=workspace_item_id)
    if visible is None:
        if request.POST['visible']:
            visible = request.POST['visible']
    if visible:
        lookup = {'true': True, 'false': False}
        workspace_item.visible = lookup[visible]
    workspace_item.save()

    return HttpResponse(json.dumps(workspace_item.workspace.id))


@never_cache
def workspace_item_delete(request, object_id=None):
    """delete workspace item from workspace

    returns workspace_id

    if workspace_item_id is not provided, it tries to get the variable
    workspace_item_id from the request.POST
    """
    if object_id is None:
        object_id = request.POST['object_id']
    workspace_item = get_object_or_404(WorkspaceItem, pk=object_id)
    workspace_id = workspace_item.workspace.id
    workspace_item.delete()

    return HttpResponse(json.dumps(workspace_id))


def session_workspace_edit_item(request,
                                workspace_item_id=None,
                                workspace_category='user'):
    """edits workspace item, the function automatically finds best appropriate
    workspace

    if workspace_item_id is None, a new workspace_item will be created using
    workspace_item_add TODO if workspace_item_id is filled in, apply edits and
    save

    """
    workspace_id = request.session['workspaces'][workspace_category][0]

    is_temp_workspace = workspace_category == 'temp'

    if workspace_item_id is None:
        return workspace_item_add(request, workspace_id,
                                  is_temp_workspace=is_temp_workspace)

    #todo: maak functie af
    return


"""
Generic popup
"""
def popup_json(found):
    """Return html with info on closest-matching fews point.

    found: list of dictionaries {'distance': ..., 'timeserie': ...,
    'workspace_item': ..., 'identifier': ..., 'url_img':...}.
    """

    # ``found`` is a list of dicts {'distance': ..., 'timeserie': ...}.
    found.sort(key=lambda item: item['distance'])
    # Grab the first one
    timeserie = found[0]['object']
    workspace_item = found[0]['workspace_item']

    # Compose html header
    header = '<div><strong>%s</strong><a href="" class="add-snippet" data-workspace-id="%d" data-workspace-item-id="%d" data-item-identifier=\'%s\' data-item-shortname="%s" data-item-name="%s">add snippet</a></div>' % (
        timeserie.name,
        workspace_item.workspace.id,
        workspace_item.id,
        simplejson.dumps(found[0]['identifier']),
        timeserie.shortname,
        timeserie.name
        )
    if not timeserie.data_count():
        body = "<div>Geen gegevens beschikbaar.</div>"
    else:
        # img = reverse("lizard_fewsunblobbed.timeserie_graph",
        #               kwargs={'id': timeserie.pk})
        body = "<div><img src='%s' /></div>" % found[0]['img_url']
    html = header + body
    x_found, y_found = coordinates.rd_to_google(timeserie.locationkey.x,
                                                timeserie.locationkey.y)
    result = {'id': 'popup-id',
              'objects': [{'html': html,
                           'x': x_found,
                           'y': y_found}, ]
              }
    return HttpResponse(simplejson.dumps(result))



"""
Collages stuff
"""

def collage(request,
              collage_id,
              template='lizard_map/collage.html'):
    """Render page with one collage"""
    return render_to_response(
        template,
        {'collage_id': collage_id},
        context_instance=RequestContext(request))

def collage_popup(request,
                  collage_id=None,
                  template='lizard_map/collage.html'):
    """Render page with one collage in popup format"""
    if collage_id is None:
        collage_id = request.GET.get("collage_id")
    return render_to_response(
        template,
        {'collage_id': collage_id},
        context_instance=RequestContext(request))


def session_collage_snippet_add(request, 
                                workspace_item_id=None,
                                workspace_item_location_identifier=None,
                                workspace_item_location_shortname=None,
                                workspace_item_location_name=None,
                                workspace_collage_id=None, 
                                workspace_category='user'):
    """finds session user workspace and add snippet to (only) corresponding
    collage
    """
    if workspace_item_id is None:
        workspace_item_id = request.POST.get('workspace_item_id')
    if workspace_item_location_identifier is None:
        workspace_item_location_identifier = request.POST.get(
            'workspace_item_location_identifier')
    if workspace_item_location_shortname is None:
        workspace_item_location_shortname = request.POST.get(
            'workspace_item_location_shortname')
    if workspace_item_location_name is None:
        workspace_item_location_name = request.POST.get(
            'workspace_item_location_name')

    workspace_id = request.session['workspaces'][workspace_category][0]
    workspace = get_object_or_404(Workspace, pk=workspace_id)
    workspace_item = get_object_or_404(WorkspaceItem, pk=workspace_item_id)

    if len(workspace.collages.all()) == 0:
        workspace.collages.create()
    collage = workspace.collages.all()[0]
    collage.snippets.get_or_create(workspace_item=workspace_item,
                                   identifier_json=workspace_item_location_identifier,
                                   shortname=workspace_item_location_shortname,
                                   name=workspace_item_location_name)

    return HttpResponse(json.dumps(workspace_id))

def session_collage_snippet_delete(request,
                                   object_id=None):
    """removes snippet

    TODO: check permission of collage, workspace owners
    """
    if object_id is None:
        object_id = request.POST.get('object_id')
    snippet = get_object_or_404(WorkspaceCollageSnippet, pk=object_id)
    snippet.delete()

    return HttpResponse()


def snippet(request, snippet_id=None):
    """get snippet/fews location by snippet_id and return data

    """
    if snippet_id is None:
        snippet_id = request.GET.get('snippet_id')
    snippet = get_object_or_404(WorkspaceCollageSnippet, pk=snippet_id)

    workspace_item = snippet.workspace_item

    return popup_json([snippet.location, ])

"""
Map stuff
"""


def wms(request, workspace_id):
    """Return PNG as WMS service."""

    workspace = get_object_or_404(Workspace, pk=workspace_id)

    # WMS standard parameters
    width = int(request.GET.get('WIDTH'))
    height = int(request.GET.get('HEIGHT'))
    layers = request.GET.get('LAYERS')
    layers = [layer.strip() for layer in layers.split(',')]
    bbox = request.GET.get('BBOX')
    bbox = tuple([float(i.strip()) for i in bbox.split(',')])
    # TODO: check that they're not none

    # Map settings
    mapnik_map = mapnik.Map(width, height)
    mapnik_map.srs = coordinates.GOOGLE
    mapnik_map.background = mapnik.Color('transparent')
    #m.background = mapnik.Color('blue')

    for workspace_item in workspace.workspace_items.filter(visible=True):
        layers, styles = workspace_item.adapter.layer()
        layers.reverse()  # first item should be drawn on top (=last)
        for layer in layers:
            mapnik_map.layers.append(layer)
        for name in styles:
            mapnik_map.append_style(name, styles[name])

    #Zoom and create image
    mapnik_map.zoom_to_box(mapnik.Envelope(*bbox))
    # m.zoom_to_box(layer.envelope())
    img = mapnik.Image(width, height)
    mapnik.render(mapnik_map, img)
    http_user_agent = request.META.get('HTTP_USER_AGENT', '')
    rgba_image = PIL.Image.fromstring('RGBA', (width, height), img.tostring())
    buf = StringIO.StringIO()
    if 'MSIE 6.0' in http_user_agent:
        imgPIL = rgba_image.convert('P')
        imgPIL.save(buf, 'png', transparency=0)
    else:
        rgba_image.save(buf, 'png')
    buf.seek(0)
    response = HttpResponse(buf.read())
    response['Content-type'] = 'image/png'
    return response


def clickinfo(request, workspace_id):
    # TODO: this one is mostly for testing, so it can be removed later on.
    # [reinout]
    workspace = get_object_or_404(Workspace, pk=workspace_id)
    # xy params from the GET request.
    x = float(request.GET.get('x'))
    y = float(request.GET.get('y'))

    found = None
    for workspace_item in workspace.workspace_items.filter(visible=True):
        found_items = workspace_item.search(x, y)
        if found_items:
            # Grab first one for now.
            found = found_items[0]
            break

    if found:
        msg = "You found %s at  %s, %s" % (found.name, found.x, found.y)
    else:
        msg = 'Nothing found'
    # TODO: return json: {msg, x_found, y_found}
    return HttpResponse(msg)
