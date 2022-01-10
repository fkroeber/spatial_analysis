library(shiny)
library(sf)
library(leafem)
library(leaflet)
library(leaflet.extras)
library(leaflet.esri)
library(raster)
library(rgdal)
library(tidyverse)

ui <- fluidPage(
  titlePanel("Views along Austrian hiking paths"),
  sidebarLayout(
    sidebarPanel(
      verbatimTextOutput("clickInfo"),
      checkboxInput("paths", "show paths", value = T, width = NULL),
      checkboxInput("points", "show viewpoints along paths", value = F, width = NULL),
      conditionalPanel(
        condition = "input.points == 1",
        splitLayout(
          cellWidths = c("10%", "90%"),
          column(width=12),
          column(width=12,
                 checkboxInput("points_colorise", "colorise viewpoints according to viewfactor", value = F, width = NULL),
                 br(),
                 p("filter points: best x% of viewpoints"),
                 sliderInput("points_filter", NULL, min = 0, max = 100, value = 100, width = NULL),
                 br()
          )
        )
      ),
      p("transparency landcover/landuse (",
        HTML("<a href=https://image.discomap.eea.europa.eu/arcgis/services/Corine/CLC2018_WM/MapServer/WmsServer?request=GetLegendGraphic%26version=1.3.0%26format=image/png%26layer=7>legend</a>"),
        ")"),
      sliderInput("landuse", NULL,
                  min = 0, max = 1, value = 1, width = NULL),
      br(),
      p("Info: Basic stats for one specific viewpoint are displayed below the map frame as",
        "soon as a single marker is selected on the map")
    ),
    mainPanel(
      verticalLayout(
        leafletOutput("map"),
        br(),
        uiOutput("stats_panel")
      )
    )
  )
)

server <- function(input, output, session) {
  
  # loads paths, points & stats
  paths = rgdal::readOGR("paths.geojson")
  paths_bbox = paths %>% st_bbox() %>% as.character()
  points = rgdal::readOGR("points.geojson")
  stats_all = read_csv("summary_stats.csv")
  print("input data loaded")

  # define static basemaps
  bmaps = c("Esri.WorldTopoMap", "Esri.WorldImagery")
  
  addBasemaps = function(x){
    for (i in bmaps){
      x = x %>% addProviderTiles(provider=i, group=i)
    }
    return(x)
  }

  output$map = renderLeaflet({
    leaflet() %>%
    fitBounds(paths_bbox[1], paths_bbox[2], paths_bbox[3], paths_bbox[4]) %>% 
    addBasemaps() %>% 
    addLayersControl(baseGroups = bmaps, 
                     options = layersControlOptions(collapsed = T, autoZIndex = F)) %>%
    addMiniMap(tiles = bmaps[[1]], toggleDisplay = F, position = "bottomleft") %>%
    htmlwidgets::onRender("
      function(el, x) {
        var myMap = this;
        myMap.on('baselayerchange',
          function (e) {
            myMap.minimap.changeLayer(L.tileLayer.provider(e.name));
          })
      }") %>% 
    addMouseCoordinates()
  })

  # add transparent landcover layer
  observe({leafletProxy("map") %>%
      clearGroup("clc_wms") %>%
      addEsriDynamicMapLayer("https://image.discomap.eea.europa.eu/arcgis/rest/services/Corine/CLC2018_WM/MapServer",
                             layerId = "clc_wms",
                             dynamicMapLayerOptions(opacity = (1-input$landuse)))})
  
  # add hiking paths
  col_paths = map(.f = ~colorNumeric("Blues", c(-100,250))(.x),
                  .x = paths$OBJECTID)
  observe({leafletProxy("map") %>%
      clearGroup("paths") %>%
      {ifelse(input$paths,
              addPolylines(.,
                           data = paths,
                           group = "paths",
                           opacity = 0.8,
                           color = unlist(col_paths),
                           label = ~paste0(route, " path: ", name)),
              .)}
      })
    
  # add (filtered & colorised) viewpoints
  best_points_geom = reactive({
    stats_all %>% 
      top_frac(input$points_filter/100, viewfactor) %>% 
      {points[points$OBJECTID %in% .$id,]}
  })
  
  best_points_viewfactors = reactive({
    stats_all %>% 
      top_frac(input$points_filter/100, viewfactor) %>% 
      .$viewfactor
  })
  
  col_markers = reactive({
    
    viewranked_ids = stats_all %>% 
      arrange(desc(viewfactor)) %>% 
      transmute(id = id, view_rank = 1:dim(stats_all)[1]) %>% 
      right_join(points, by=c("id" = "OBJECTID"), copy=T) %>% 
      drop_na(view_rank) %>% 
      filter(id %in% best_points_geom()$OBJECTID) %>%
      arrange(id) %>%
      .$view_rank
    
    ifelse(input$points_colorise,
           list(unlist(map(.x = viewranked_ids, 
                           .f = ~colorNumeric(rev("RdYlGn"), 
                                              c(0, dim(stats_all)[1]),
                                              reverse = T)(.x)))),
           list("#838383"))
  })
  
  observe({
    leafletProxy("map") %>%
      clearGroup("points_multiple") %>% 
      {ifelse(input$points, 
              addCircleMarkers(., 
                         data = best_points_geom(),
                         layerId = best_points_geom()$OBJECTID,
                         label = sprintf("point_id: %s<br/>
                                         <strong>viewfactor: %.3f</strong><br/>",
                                         best_points_geom()$OBJECTID, 
                                         best_points_viewfactors()) %>%
                                 lapply(htmltools::HTML),
                         color = unlist(col_markers()),
                         opacity = 0.8,
                         fillOpacity = 0.5,
                         group = "points_multiple"), 
              .)}
      })
  
  # register map clicks
  data <- reactiveValues(clickedMarker=NULL)
  clicklist <- reactiveVal(list())
  
  observeEvent(input$map_click, {
    click <- input$map_click
    temp <- clicklist()
    temp[[length(temp)+1]] <- click
    clicklist(temp)
    if (length(unlist(rev(clicklist())[1])) == 3 & 
        length(unlist(rev(clicklist())[2])) == 3) 
       {data$clickedMarker = NULL}
    })
  
  observeEvent(input$map_marker_click, {
    click <- input$map_marker_click
    temp <- clicklist()
    temp[[length(temp)+1]] <- click
    clicklist(temp)
    data$clickedMarker = input$map_marker_click
    })

  # define behaviour in case of marker clicks
  # click: zoom to point, show viewhshed raster and corresponding stats
  # unclick: show all viewpoints again
  observe({
    view_point = data$clickedMarker
    if (is.null(view_point)) {
      leafletProxy("map") %>%
        clearGroup("points_multiple") %>%
        clearGroup("point_single") %>%
        clearGroup("viewshed_raster") %>%
        clearGroup("circles") %>%
        clearControls()
      
      if (length(clicklist()) > 0){
        updateCheckboxInput(session, "points", value = T)
      }
      
      output$stats_panel = NULL
    } else {
      updateCheckboxInput(session, "points", value = F)
      
      viewshed = raster(paste0("viewshed_", view_point$id, ".tif"))
      view_point_geom = points[points$OBJECTID==view_point$id,]
      view_point_stat = stats_all[stats_all$id==view_point$id,]
      
      point_viewfactor = view_point_stat$viewfactor
      point_viewarea = view_point_stat$area_vis
      
      landuse_stats_level_1 = map(
        .f = ~sum(stats_all[stats_all$id==view_point$id,] %>% select(starts_with(.x)), na.rm = T)*625/10000/
          (stats_all[stats_all$id==view_point$id,]$area_vis),
        .x = c("clc_1", "clc_2", "clc_3", "clc_4", "clc_5")
      )
      
      paths_point_ids = points@data %>%
        group_by(route,name) %>%
        group_map(~.x$OBJECTID)
      
      output$stats_panel = renderUI({
        splitLayout(
          column(width=12,
                 p(icon("eye-open", lib="glyphicon"), HTML("&nbsp;viewfactor compared to other locations")),
                 renderPlot(height=200, ggplot() +
                              map2(.f = ~geom_density(data=stats_all %>% filter(id %in% .x),
                                                      aes(x=viewfactor),
                                                      colour=.y,
                                                      size=1),
                                   .x = paths_point_ids,
                                   .y = col_paths) +
                              geom_vline(aes(xintercept=view_point_stat$viewfactor),
                                         linetype="dashed",
                                         colour="black",
                                         size=1) +
                              geom_text(aes(x=view_point_stat$viewfactor, y=40,
                                            label="\ncurrent"),
                                        colour="black",
                                        angle=90,
                                        text=element_text(size=11)) +
                              #scale_y_sqrt() +
                              labs(y = "density") +
                              theme_classic())
          ),
          column(width=12,
                 p(icon("globe", lib="glyphicon"), HTML("&nbsp;landuse/landcover within the field of vision")),
                 renderText({sprintf("artificial surfaces: %.1f%%", landuse_stats_level_1[[1]])}),
                 renderText({sprintf("agricultural surfaces: %.1f%%", landuse_stats_level_1[[2]])}),
                 renderText({sprintf("forests and seminatural surfaces: %.1f%%", landuse_stats_level_1[[3]])}),
                 renderText({sprintf("wetlands: %.1f%%", landuse_stats_level_1[[4]])}),
                 renderText({sprintf("water bodies: %.1f%%", landuse_stats_level_1[[5]])})
          )
        )
      })
      
      leafletProxy("map") %>%
        clearGroup("points_multiple") %>%
        clearGroup("point_single") %>%
        clearGroup("viewshed_raster") %>%
        clearGroup("circles") %>%
        clearControls() %>%
        addCircleMarkers(data = view_point_geom,
                         color = col_markers(),
                         opacity = 0.8,
                         fillOpacity = 0.5,
                         group = "point_single") %>%
        addGreatCircles(view_point$lat,
                        view_point$lng,
                        label="40 km radius",
                        steps=200,
                        color="#000000",
                        group="circles",
                        weight=2,
                        radius=40000) %>%
        addGeoRaster(x=viewshed, 
                     project = F, 
                     group = "viewshed_raster",
                     autozoom = F,
                     colorOptions = leafem:::colorOptions(palette=c(rgb(0, 0, 0, max = 255, alpha = 0), "#000000"))) %>%
        flyTo(view_point$lng, view_point$lat, zoom=12) %>%
        addLegend(position = "bottomright",
                  colors = "#000000",
                  labels = "visible areas",
                  group = "viewshed_raster",
                  opacity = 1,
                  title = NULL)
      
    }
  })

}

shinyApp(ui, server)

