### Preliminaries ###
# Import system modules
import arcpy
from arcpy import env
import os
import datetime
import pandas as pd

# Set environment settings
input_gdb = arcpy.mp.ArcGISProject('current').defaultGeodatabase
#output_gdb = arcpy.management.CreateFileGDB(arcpy.mp.ArcGISProject('current').homeFolder, "output_vhp.gdb")
#env.workspace = os.path.join(arcpy.mp.ArcGISProject('current').homeFolder, "output_vhp.gdb")
#env.overwriteOutput = True

# Helper function to get feature table as pd
# Credits go to https://gist.github.com/d-wasserman/e9c98be1d0caebc2935afecf0ba239a0   
def arcgis_table_to_df(in_fc, input_fields=None, query=""):
    OIDFieldName = arcpy.Describe(in_fc).OIDFieldName
    if input_fields:
        final_fields = [OIDFieldName] + input_fields
    else:
        final_fields = [field.name for field in arcpy.ListFields(in_fc)]
    data = [row for row in arcpy.da.SearchCursor(in_fc,final_fields,where_clause=query)]
    fc_dataframe = pd.DataFrame(data,columns=final_fields)
    fc_dataframe = fc_dataframe.set_index(OIDFieldName,drop=True)
    return fc_dataframe

### Part I - Food Collection ###
# Set local variables for VRP layer
network = "https://www.arcgis.com/"
layer_name = "vrp_food_collection"
travel_mode = "Driving Time"
time_units = "Minutes"
distance_units = "Kilometers"

in_depots = os.path.join(input_gdb, "storage_location")

in_orders = os.path.join(input_gdb, "supermarkets_targeted")
arcpy.management.CalculateField(in_orders, field='name_osmid', expression='!name!.replace(" ","") + "_" + !osm_id!')


# Create a new Vehicle Routing Problem (VRP) object
# Get the VRP layer & sublayer names
result_object = arcpy.na.MakeVehicleRoutingProblemAnalysisLayer(network, layer_name, travel_mode, time_units,
                                                                distance_units, line_shape="ALONG_NETWORK",
                                                                spatial_clustering="CLUSTER")
layer_object = result_object.getOutput(0)
sub_layer_names = arcpy.na.GetNAClassNames(layer_object)

# Add infos to single layers of VRP
arcpy.na.AddLocations(layer_object, "Depots", in_depots, append="CLEAR")

order_field_mappings = arcpy.na.NAClassFieldMappings(layer_object, "Orders", False, arcpy.ListFields(in_orders))
order_field_mappings["Name"].mappedFieldName = "name_osmid"
order_field_mappings["ServiceTime"].defaultValue = 10
order_field_mappings["TimeWindowStart"].defaultValue = "6 PM"
order_field_mappings["TimeWindowEnd"].defaultValue = "10 PM"
arcpy.na.AddLocations(layer_object, "Orders", in_orders, order_field_mappings, append="CLEAR")

arcpy.na.AddVehicleRoutingProblemRoutes(layer_object, number_of_routes=30,
                                        start_depot_name="central_storage",
                                        end_depot_name="central_storage",
                                        max_order_count=20,
                                        earliest_start_time="5 PM",
                                        latest_start_time="10 PM",
                                        append_to_existing_routes="CLEAR")
arcpy.management.CalculateField(arcpy.na.GetNASublayer(layer_name, 'Routes'),
                                field='EndDepotServiceTime', expression=30)

# Solve the VRP layer
arcpy.na.Solve(layer_object)



### Part II - Food Delivery ###
arcpy.management.CreateRandomPoints(out_path=env.workspace,
                                    out_name="potential_customers",
                                    constraining_feature_class="inhabitants_18_65",
                                    number_of_points_or_field="a_18_65_aa")
n_potential_customers = int(arcpy.management.GetCount('potential_customers').getOutput(0))

scenarios = {'ncust25': 25, 'ncust250': 250}
n_simulations = 3

res_routes = []

for sim in range(n_simulations):
    for scen, ncust in scenarios.items():
        # simulate locations of customers
        arcpy.ga.SubsetFeatures(in_features="potential_customers",
                                out_training_feature_class="customers_sim{}_{}".format(sim, scen),
                                size_of_training_dataset=ncust,
                                subset_size_units="ABSOLUTE_VALUE")


        # defining new travel mode
        travel_modes = arcpy.nax.GetTravelModes(network)
        bicycle_travel = arcpy.na.TravelMode(travel_modes["Walking Time"])
        bicycle_travel.name = "Bicycle Time"
        bicycle_travel.attributeParameters[('WalkTime', 'Walking Speed (km/h)')] = 25.0 # does not work, see workaround below

        # Set local variables for VRP layer
        network = "https://www.arcgis.com/"
        layer_name = "vrp_delivery_sim{}_{}".format(sim, scen)
        travel_mode = bicycle_travel
        time_units = "Minutes"
        distance_units = "Kilometers"
        
        in_depots = os.path.join(input_gdb, "storage_location")
        in_orders = "customers_sim{}_{}".format(sim, scen)

        # Create a new Vehicle Routing Problem (VRP) object
        # Get the VRP layer & sublayer names
        result_object = arcpy.na.MakeVehicleRoutingProblemAnalysisLayer(network, layer_name, travel_mode, time_units,
                                                                        distance_units, line_shape="ALONG_NETWORK",
                                                                        spatial_clustering="CLUSTER")
        layer_object = result_object.getOutput(0)
        sub_layer_names = arcpy.na.GetNAClassNames(layer_object)

        # Configure inputs of VRP layer
        arcpy.na.UpdateAnalysisLayerAttributeParameter(layer_object, "WalkTime", "Walking Speed (km/h)", 25.0) #workaround
        arcpy.na.AddLocations(layer_object, "Depots", in_depots, append="CLEAR")

        order_field_mappings = arcpy.na.NAClassFieldMappings(layer_object, "Orders", False, arcpy.ListFields(in_orders))
        order_field_mappings["Name"].mappedFieldName = "OID"
        order_field_mappings["ServiceTime"].defaultValue = 5
        order_field_mappings["TimeWindowStart"].defaultValue = "8 AM"
        order_field_mappings["TimeWindowEnd"].defaultValue = "8 PM"
        arcpy.na.AddLocations(layer_object, "Orders", in_orders, order_field_mappings, append="CLEAR")

        arcpy.na.AddVehicleRoutingProblemRoutes(layer_object, number_of_routes=500,
                                                start_depot_name="central_storage",
                                                end_depot_name="central_storage",
                                                max_order_count=10,
                                                earliest_start_time="7 AM",
                                                latest_start_time="8 PM",
                                                append_to_existing_routes="CLEAR")
        arcpy.management.CalculateField(arcpy.na.GetNASublayer(layer_name, 'Routes'),
                                        field='StartDepotServiceTime', expression=15)

        # Solve the VRP layer
        arcpy.na.Solve(layer_object)

        # Convert & Save results to pd.Dataframe
        routes = arcgis_table_to_df(arcpy.na.GetNASublayer(layer_name, 'Routes'))
        routes = routes[routes['OrderCount'] > 0]
        routes.insert(0, 'sim_name', len(routes)*["vrp_delivery_sim{}_{}".format(sim, scen)])
        res_routes.append(routes)
        

# Compare results statistically    
res_routes = pd.concat(res_routes)
(res_routes.groupby(['sim_name'])
           .agg(time_total = ('TotalTime', sum),
                dist_total = ('TotalDistance', sum)))

res_routes.to_csv(os.path.join(arcpy.mp.ArcGISProject('current').homeFolder, 'stats_routes.csv'))


# get service area stats
service_area_stats = arcgis_table_to_df("Polygons_TabulateIntersectio").sort_values(['PERCENTAGE'], ascending=False)
service_area_stats['sum_18_65_aa'] = service_area_stats['a_18_65_aa'].cumsum()
service_area_stats = service_area_stats.loc[:, ['Name', 'a_18_65_aa', 'sum_18_65_aa']]
service_area_stats.to_csv(os.path.join(arcpy.mp.ArcGISProject('current').homeFolder, 'stats_serviceareas.csv'))




           
