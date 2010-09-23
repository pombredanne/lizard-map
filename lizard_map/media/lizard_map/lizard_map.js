// jslint configuration; btw: don't put a space before 'jslint' below.
/*jslint browser: true */
/*global $, OpenLayers, window, updateLayer, updateLayers,
stretchOneSidebarBox, reloadGraphs, fillSidebar, show_popup,
hover_popup */

var animationTimer;

// if (typeof(console) === 'undefined') {
//     // Prevents the firebug console from throwing errors in browsers other
//     // than Firefox/Chrome/Chromium
//     // From http://gist.github.com/384113
//     var console = {};
//     console.log = function () {};
// }


function setSliderDate(slider_value) {
    $.ajax({
        type: "POST",
        url: $("#animation-slider").attr("data-ajax-path"),
        data: "slider_value=" + slider_value,
        success: function (data) {
            // Update the date label span with the returned data
            $('span#selected-date').html($.parseJSON(data));
        }
    });
}


function setUpAnimationSlider() {
    var workspace_item_id;
    $("#animation-slider").slider({
        min: parseInt($("#animation-slider").attr("data-min"), 10),
        max: parseInt($("#animation-slider").attr("data-max"), 10),
        step: parseInt($("#animation-slider").attr("data-step"), 10),
        value: parseInt($("#animation-slider").attr("data-value"), 10),
        slide: function (event, ui) {
            if (animationTimer) {
                clearTimeout(animationTimer);
            }
            animationTimer = setTimeout(
                function () {
                    // Only run after nothing happened for 300ms.
                    setSliderDate(ui.value);
                },
                300);
        },
        change: function (event, ui) {
            setSliderDate(ui.value);
            updateLayers();
            reloadGraphs();
        }
    });
}


function reloadMapActions() {
    $(".map-actions").load(
        "./ .map-action",
        function () {
            fillSidebar();
            setUpAnimationSlider();
        });
}


function setUpWorkspaceAcceptable() {
    // Set up draggability for current and future items.
    // See http://tinyurl.com/29lg4y3 .
    $(".workspace-acceptable").live("mouseover", function () {
        if (!$(this).data("draggable-initialized")) {
            $(this).data("draggable-initialized", true);
            $(this).draggable({
                scroll: 'false',
                cursor: 'move',
                helper: 'clone',
                appendTo: 'body',
                revert: 'true',
                placeholder: 'ui-sortable-placeholder'
            });
        }
    });
    // Clicking a workspace-acceptable shows it in the 'temp' workspace.
    $(".workspace-acceptable").live("click", function (event) {
        var name, adapter_class, adapter_layer_json, url_add_item_temp;
        $(".workspace-acceptable").removeClass("selected");
        $(this).addClass("selected");
        name = $(this).attr("data-name");
        adapter_class = $(this).attr("data-adapter-class");
        adapter_layer_json = $(this).attr("data-adapter-layer-json");
        url_add_item_temp = $(".workspace").attr(
            "data-url-lizard-map-session-workspace-add-item-temp");
        $.post(
            url_add_item_temp,
            {name: name,
             adapter_class: adapter_class,
             adapter_layer_json: adapter_layer_json
            },
            function (workspace_id) {
                updateLayer(workspace_id);
                reloadMapActions();
            });
        stretchOneSidebarBox();
    });
}

// Date selector: only support for ONE date selector at the moment.

function setUpDateChoice() {
    $.datepicker.setDefaults($.datepicker.regional.nl);
    $("#id_date_start").datepicker();
    $("#id_date_end").datepicker();
}

function setUpDateAjaxForm(overlay) {
    var form = $("form", overlay);
    form.submit(function () {
        $.post(
            form.attr('action'),
            form.serialize(),
            function (data) {
                // This is the success handler.  Form can contain errors,
                // though.
                var newForm, freshForm;
                newForm = $(data).find('form');
                form.html(newForm);
                setUpDateChoice();
                freshForm = $("form", overlay);
                setUpDateAjaxForm(freshForm);
                if (newForm.html().indexOf("rror") === -1) {
                    // No error/Error, so refresh graphs and close the popup.
                    reloadGraphs();
                    $("div.close", overlay).click();
                }
            });
        return false;
    });
}


function setUpDatePopup() {
    $(".popup-trigger").live('mouseover', function () {
        if (!$(this).data("popup-initialized")) {
            $(this).data("popup-initialized", true);
            $(this).overlay({
                onLoad: function () {
                    var overlay = this.getOverlay();
                    setUpDateAjaxForm(overlay);
                }
            });
        }
    });
}

function setUpNotFoundPopup() {
    $("#not_found_popup_trigger").overlay();
}

function nothingFoundPopup() {
    $("#not_found_popup_trigger").click();
    setTimeout(function () {
        $("#not_found_popup div.close").click();
    },
              2000);
}

function setUpGraphEditPopup() {
    $(".graph_edit_trigger").overlay();
}


/*
Shows legend tooltip. Re-initializes after workspace update.
*/
function setUpLegendTooltips() {
    $(".legend-tooltip").each(function () {
        if (!$(this).data("popup-initialized")) {
            $(this).data("popup-initialized", true);
            $(this).tooltip({
                position: 'center right',
                effect: 'fade',
                onShow: function () {
                    var offset, pixels_below_screen;
                    // Too high?
                    offset = this.getTip().offset();
                    if (offset.top < 0) {
                        offset.top = 0;
                        this.getTip().offset(offset);
                    }
                    // Too low?
                    pixels_below_screen = offset.top +
                        this.getTip().height() -
                        $(window).height();
                    if (pixels_below_screen > 0) {
                        offset.top = offset.top - pixels_below_screen;
                        this.getTip().offset(offset);
                    }
                    // Repositioning beforehand would be visually nicer.
                }
            });
        }
    });
}




/*
Empty the temp workspace
*/
function setUpEmptyTempInteraction() {
    $(".workspace-empty-temp").live("click", function () {
        var $workspace, url, workspace_item_id;
        $(this).css("cursor", "progress");
        $workspace = $(".workspace");
        url = $workspace.attr("data-url-lizard-map-workspace-item-delete");
        workspace_item_id = $(this).attr("data-workspace-item-id");
        $.post(
            url,
            {object_id: workspace_item_id},
            function (workspace_id) {
                updateLayer(workspace_id);
                // load new map actions
                reloadMapActions();
                // remove highlighting
                $(".workspace-acceptable").removeClass("selected");
            }
        );
    });
}


function popup_click_handler(x, y, map) {
    var extent, radius, url;
    extent = map.getExtent();
    radius = Math.abs(extent.top - extent.bottom) / 30;  // Experimental, seems to work good
    $("#map_OpenLayers_ViewPort").css("cursor", "progress");
    url = $(".workspace").attr("data-url-lizard-map-search-coordinates");
    if (url !== undefined) {
        $.getJSON(
            url,
            { x: x, y: y, radius: radius },
            function (data) {
                $("#map_OpenLayers_ViewPort").css("cursor", "default");
                show_popup(data, map);
            }
        );
    }
}


function popup_hover_handler(x, y, map) {
    /* Show name of one item when hovering above a map */
    var extent, radius, url;
    extent = map.getExtent();
    radius = Math.abs(extent.top - extent.bottom) / 30;  // experimental, seems to work good
    url = $(".workspace").attr("data-url-lizard-map-search-name");
    if (url !== undefined) {
        $.getJSON(
            url,
            { x: x, y: y, radius: radius },
            function (data) {
                hover_popup(data, map);
            }
        );
    }
}


function legend_action_reload(event) {
    // send all legend properties to server and reload page
    var $form, url, name;
    event.preventDefault();
    $form = $(this).parents("form.legend-options");
    url = $form.attr("data-url");
    $.post(
        url,
        $form.serialize(),
        function () {
            // Reload page after posting.
            location.reload();
        });
}


function setUpLegendColorPickers() {
    var submit, beforeshow;
    submit = function (hsb, hex, rgb, el) {
	    $(el).val(hex);
	    $(el).ColorPickerHide();
    };
    beforeshow = function () {
        $(this).ColorPickerSetColor(this.value);
    };

    $("input[name=min_color]").ColorPicker({onSubmit: submit, onBeforeShow: beforeshow});
    $("input[name=max_color]").ColorPicker({onSubmit: submit, onBeforeShow: beforeshow});
    $("input[name=too_low_color]").ColorPicker({onSubmit: submit, onBeforeShow: beforeshow});
    $("input[name=too_high_color]").ColorPicker({onSubmit: submit, onBeforeShow: beforeshow});

    // Setup widget colors.
    $("#colorSelector").each(function () {
        var div, rel, color;
        rel = $(this).attr("rel");
        color = $(rel).attr("value");
        div = $(this).find("div");
        div.css('backgroundColor', '#' + color);
    });

    // Make the widget clickable.
    $("#colorSelector").ColorPicker({
        onBeforeShow: function () {
            var rel, color;
            rel = $(this).attr("rel");
            color = $(rel).attr("value");
            $(this).ColorPickerSetColor(color);
        },
        onChange: function (hsb, hex, rgb) {
            $("#colorSelector div").css('backgroundColor', '#' + hex);
        },
        onSubmit: function (hsb, hex, rgb, el) {
            var rel;
            rel = $(el).attr("rel");
            $(rel).val(hex);
            $(el).ColorPickerHide();
        }
    });

}


function setUpLegendEdit() {
    $(".legend-edit").live("mouseover", function () {
        if (!$(this).data("popup-initialized")) {
            $(this).data("popup-initialized", true);
            $(this).overlay();
            setUpLegendColorPickers();
        }
    });
    $(".legend-action-reload").live("click", legend_action_reload);
}


// Initialize all workspace actions.
$(document).ready(function () {
    setUpWorkspaceAcceptable();
    setUpDatePopup();
    setUpDateChoice();
    setUpNotFoundPopup();
    setUpEmptyTempInteraction();
    setUpAnimationSlider();
    setUpGraphEditPopup();

    // Set up legend.
    setUpLegendTooltips(); // The edit function is on the tooltip.
    setUpLegendEdit();

    /* Workspace functions, requires jquery.workspace.js */
    $(".workspace").workspaceInteraction();
    $(".add-snippet").snippetInteraction(); // voor collage view, nu nog nutteloos voor popup
    // $("a.lizard-map-link").lizardMapLink();
});
