// jslint configuration
/*jslint browser: true */
/*global $, OpenLayers, window, updateLayer, stretchOneSidebarBox,
reloadGraphs, fillSidebar */

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
                $(".map-actions").load("./ .map-action",
                                       fillSidebar);
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

/*
Empty the temp workspace
*/
function setUpEmptyTempInteraction() {
    $("span.workspace-empty-temp").live("click", function () {
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
                $(".map-actions").load("./ .map-action",
                                       fillSidebar);
                // remove highlighting
                $(".workspace-acceptable").removeClass("selected");
            }
        );
    });
}


function loadSizedImages() {
    var height, width;
    // height = verticalItemHeight;
    height = '';
    width = mainContentWidth;
    $('a.replace-with-image').each(
        function (index) {
            var url, timestamp;
            $(this).hide();
            url = $(this).attr('href');
            $('~ img', this).remove();
            timestamp = new Date().getTime();  // No cached images.
            // dit doet het niet goed bij urls met json {" ... "}
            $(this).after('<img src="' + url + '?width=' + width + '&height=' +
                          height + '&random=' + timestamp + '" />');
        }
    );    
}


// Initialize all workspace actions.
$(document).ready(function () {
    setUpWorkspaceAcceptable();
    setUpDatePopup();
    setUpDateChoice();
    setUpNotFoundPopup();
    setUpEmptyTempInteraction();
    /* Workspace functions, requires jquery.workspace.js */
    $(".workspace").workspaceInteraction();
    // $(".add-snippet").snippetInteraction(); // als het met live werkt kan het hier
    // $("a.lizard-map-link").lizardMapLink();
    loadSizedImages();
});
