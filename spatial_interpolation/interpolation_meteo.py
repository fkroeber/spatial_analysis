# start grass project
grass78 -c epsg:32632 "grassdata/interpol_temp" -e
grass78 "grassdata/interpol_temp/PERMANENT"

working_dir='/home/felix/Desktop/assigment_interpolation'
export working_dir

python3
import os
import grass.script as gs
import numpy as np
import pandas as pd
import random
import wget
from tqdm import tqdm
from grass.pygrass.modules import Module, MultiModule, ParallelModuleQueue

# Select 2 out of all 288 files for further analyses
temp_files = os.listdir(os.path.join(os.environ['working_dir'], 'temp_prep_data'))
random.seed(234)
selected_files = random.sample(temp_files, 2)

# Importing them as vector & raster layer
temp_layers = []
for temp_file in selected_files:
    layer_name = temp_file.split('.')[0]
    gs.run_command('v.in.ascii', input=os.path.join(os.environ['working_dir'], 'temp_prep_data', temp_file),
                   output=layer_name, x=10, y=11, skip=1, cat=1, overwrite=True, columns="""cat integer, 
                   station_id integer, time varchar(25), temp double precision, height integer, 
                   latitude double precision, longitude double precision, station_name varchar(50), 
                   federal_state varchar(50), x_coord double precision, y_coord double precision""")
    gs.run_command('g.region', vector=layer_name, res=2000, flags='a')
    gs.run_command('v.extract', input=layer_name, output="{}_filtered".format(layer_name), 
                   where="temp > -999", overwrite=True)
    gs.run_command('g.rename', vector="{0}_filtered,{0}".format(layer_name), overwrite=True)
    gs.run_command('v.to.rast', input=layer_name, output=layer_name, use='attr', 
                   attribute_column='temp', type='point', overwrite=True)
    temp_layers.append(layer_name)

# Importing boundary mask
download_dir = os.path.join(os.environ['working_dir'], 'boundaries')
#import zipfile as zipfile
#wget.download('https://daten.gdz.bkg.bund.de/produkte/vg/vg1000_ebenen_0101/aktuell/vg1000_01-01.utm32s.shape.ebenen.zip', download_dir)
#zip = zipfile.ZipFile(os.path.join(download_dir, 'vg1000_01-01.utm32s.shape.ebenen.zip'), 'r')
#zip.extractall(download_dir)
#os.remove(os.path.join(download_dir, 'vg1000_01-01.utm32s.shape.ebenen.zip'))

gs.run_command('v.import', input='{}/vg1000_01-01.utm32s.shape.ebenen/vg1000_ebenen_0101/VG1000_LAN.shp'.format(download_dir), 
               output='borders_germany_', flags='o', overwrite=True)
gs.run_command('v.extract', input='borders_germany_', output='borders_germany', where="GF = 4", overwrite=True)
gs.run_command('g.region', vector='borders_germany', res=2000, flags='ap')
gs.run_command('v.to.rast', input='borders_germany', output='borders_germany', use='cat', overwrite=True)

# Defining parameter set for idw
mapset = gs.gisenv()['MAPSET']
power = np.arange(0.5, 3, 0.25)
npoints = np.arange(1, 20, 2)

# Calculating idw interpolation
gs.run_command('r.mask', raster='borders_germany')

v_surf_idw = Module('v.surf.idw', run_=False)
r_colors = Module('r.colors', run_=False)
idw_layers = []

for temp_layer in temp_layers:
    for pow in power:
        for npoi in npoints:
            nprocs = min(os.cpu_count(), len(npoints))
            queue = ParallelModuleQueue(nprocs)
            idw_name = "{}_idw_pow{}_npoi{}".format(temp_layer, pow, npoi).replace(".", "")
            idw_layers.append(idw_name)
            idw = v_surf_idw(input=temp_layer, column='temp', npoints=npoi, power=pow, output=idw_name)
            colorise = r_colors(map=idw_name, rules=os.path.join(os.environ['working_dir'], 'celsius_colorramp.txt'))
            process_chain = MultiModule([idw, colorise])
            queue.put(process_chain)


# Calculating stats for interpolated rasters
idw_stats = []

for idw_layer in idw_layers:
    single_layer_stats = {}
    single_layer_stats['name'] = idw_layer
    # simple univariate statistics
    simple_stats = gs.parse_command('r.univar', map=idw_layer, flags='t')
    idx_min = [i.split('|') for i in simple_stats.keys()][0].index('min')
    idx_max = [i.split('|') for i in simple_stats.keys()][0].index('max')
    idx_mean = [i.split('|') for i in simple_stats.keys()][0].index('mean')
    idx_sd = [i.split('|') for i in simple_stats.keys()][0].index('stddev')
    single_layer_stats['min'] = [i.split('|')[idx_min] for i in simple_stats.keys()][1]
    single_layer_stats['max'] = [i.split('|')[idx_max] for i in simple_stats.keys()][1]
    single_layer_stats['mean'] = [i.split('|')[idx_mean] for i in simple_stats.keys()][1]
    single_layer_stats['sd'] = [i.split('|')[idx_sd] for i in simple_stats.keys()][1]
    # autocorrelation measures
    gs.mapcalc("{0}_int = int({0})".format(idw_layer), overwrite=True)
    autocor_stats = gs.parse_command('r.object.spatialautocor', method = 'moran',
                                     object_map = "{}_int".format(idw_layer), 
                                     variable_map = "{}_int".format(idw_layer))
    single_layer_stats['moran'] = float(list(autocor_stats.keys())[0])
    autocor_stats = gs.parse_command('r.object.spatialautocor', method = 'geary',
                                     object_map = "{}_int".format(idw_layer), 
                                     variable_map = "{}_int".format(idw_layer))
    single_layer_stats['geary'] = float(list(autocor_stats.keys())[0])
    # texture/entropy measures
    gs.run_command('r.texture', input=idw_layer, output=idw_layer, size=3, method='entr', overwrite=True)
    entropy_stats = gs.parse_command('r.univar', map="{}_Entr".format(idw_layer), flags='t')
    idx_min = [i.split('|') for i in entropy_stats.keys()][0].index('mean')
    single_layer_stats['entropy_mean'] = [i.split('|')[idx_min] for i in entropy_stats.keys()][1]
    # cleanup & writing to idw_stats
    gs.run_command('g.remove', type='raster', name="{}_int".format(idw_layer), flags='f')
    gs.run_command('g.remove', type='raster', name="{}_Entr".format(idw_layer), flags='f')
    idw_stats.append(single_layer_stats)

pd.DataFrame(idw_stats).to_csv(os.path.join(os.environ['working_dir'], 'results', 'interpolation_stats.csv'))
# write to data frame and export

# Performing cross-validation
# Create leave-one-out layers

loo_layers = {}
for temp_layer in temp_layers:
    loo_layers[temp_layer] = {}
    loo_layers[temp_layer]['pos'] = []
    loo_layers[temp_layer]['neg'] = []
    layer_attr = gs.parse_command('db.select', sql="select * from {}".format(temp_layer))
    idx_stationid = [i.split('|') for i in layer_attr.keys()][0].index('station_id')
    station_ids = [i.split('|')[idx_stationid] for i in layer_attr.keys()][1:]
    station_ids = [int(x) for x in station_ids]
    np.random.seed(123)
    np.random.shuffle(station_ids)
    splits = np.array_split(station_ids, 50)
    for i, split in enumerate(splits):
        gs.run_command('v.extract', input=temp_layer, output="{}_pos_{}".format(temp_layer, i),
                        where="station_id not in {}".format(str(list(split)).replace("[", "(").replace("]", ")")))
        gs.run_command('v.extract', input=temp_layer, output="{}_neg_{}".format(temp_layer, i),
                        where="station_id in {}".format(str(list(split)).replace("[", "(").replace("]", ")")))
        loo_layers[temp_layer]['pos'].append("{}_pos_{}".format(temp_layer, i))
        loo_layers[temp_layer]['neg'].append("{}_neg_{}".format(temp_layer, i))

# Perform interpolation for each parameter combination for all loo_layers
# Compare results to measured values

r_mapcalc = Module("r.mapcalc", run_=False)
g_remove = Module('g.remove', run_=False)

# Create diff layers
for temp_layer in temp_layers:
    for pow in power:
        for npoi in npoints:
            for i, loo_layer in enumerate(loo_layers[temp_layer]['pos']):
                nprocs = min(os.cpu_count(), len(npoints))
                queue = ParallelModuleQueue(nprocs)
                idw_name = "idw_{}_{}_{}".format(pow,npoi,i).replace(".", "")
                idw = v_surf_idw(input=loo_layer, column='temp', npoints=npoi, power=pow, output=idw_name, overwrite=True)
                diff_name = "{}_val_pow{}_npoi{}_{}".format(temp_layer,pow,npoi,i).replace(".", "")
                diff_calc = r_mapcalc("{} = {}-{}".format(diff_name, idw_name, temp_layer), overwrite=True)
                cleanup = g_remove(type='raster', name=idw_name, flags='f')
                process_chain = MultiModule([idw, diff_calc])
                queue.put(process_chain)
            


# Populate diff results in df
def extract_diffs(idw_layer):
    val_layers = gs.list_grouped('raster', pattern="{}_*".format(idw_layer.replace("idw", "val")))[mapset]
    temp_layers = []
    for val_layer in val_layers:
        temp_prefix = val_layer.split("_")[0]
        val_suffix = val_layer.rsplit("_",1)[1]
        temp_layer = "{}_neg_{}".format(temp_prefix, val_suffix)
        temp_layers.append(temp_layer)
        gs.run_command('v.what.rast', map=temp_layer, raster=val_layer, column='diff_meas_interpol')
    gs.run_command('v.patch', input=temp_layers, output="{}_val".format(idw_layer), flags='e', overwrite=True)
    tmp_file = os.path.join(gs.gisenv()['GISDBASE'], gs.gisenv()['LOCATION_NAME'], gs.gisenv()['MAPSET'], '.tmp', gs.tempname(length=10)) + '.csv'
    gs.run_command('db.out.ogr', input="{}_val".format(idw_layer), output=tmp_file, overwrite=True)
    val_idw_layer = pd.read_csv(tmp_file)
    val_idw_layer = val_idw_layer.loc[:,['station_name', 'federal_state', 'height', 'temp', 'diff_meas_interpol']]
    val_idw_layer.insert(0, 'idw_layer', idw_layer)
    return val_idw_layer


idw_validation = []
for idw_layer in tqdm(idw_layers):
    idw_validation.append(extract_diffs(idw_layer))

pd.concat(idw_validation).to_csv(os.path.join(os.environ['working_dir'], 'results', 'interpolation_validation.csv'))















# Export interpolation maps
gs.run_command('g.region', vector='borders_germany', res=200, flags='ap')
for idw_layer in tqdm(idw_layers):
    r_info = gs.parse_command('r.info', map=idw_layer.split("_")[0], flags='rg')
    range_min = 5 * np.floor(float(r_info['min'])/5)
    range_max = 5 * np.ceil(float(r_info['max'])/5)
    outdir_layer = os.path.join(os.environ['working_dir'], 'results', idw_layer)
    outdir_legend = os.path.join(os.environ['working_dir'], 'results', "{}_legend.png".format(idw_layer))
    gs.run_command('r.out.png', input=idw_layer, compression=9, output=outdir_layer)
    gs.run_command('r.out.legend', raster=idw_layer, filetype='cairo', dimension='1.25,12.5', fontsize=12, 
                   font='Arial:Bold', flags='d', range=[range_min, range_max], label_step=5, file=outdir_legend)
    os.system("convert +append {0}.png {1} {0}.png".format(outdir_layer, outdir_legend))
    os.system("rm {}".format(outdir_legend))





### Code snippets ###

## To-Do later on
# 1. implement sklearn crossvalidation capabilities & view under aspect of permutation and combinatorics
# 2. Implement enhanced parallelising capabilities (poolmap)
# 3. Extend to other interpolation techniques
# 4. Look how cross-val is already implemented in other methods
# 5. Change to oo-programming to allow for flexibility
# 6. Increase file limit    

# Export as png, use imagemagick for animation
    # mogrify -crop 400x479+200+0 prec*.png
    # convert -delay 50 prec* precipitation_animation.gif
    # convert precipitation_animation.gif -coalesce -repage 0x0 -crop 370x479+215+0 +repage precipitation_animation.gif

# Adding changing between discrete and continous colorisation
# Preparing legend
range_val = {} 
for count, temp_layer in enumerate(temp_layers):
    r_info = gs.parse_command('r.info', map=temp_layer, flags='rg')
    if count == 0:
        range_val['min'] = float(r_info['min'])
        range_val['max'] = float(r_info['max'])
    else:
        range_val['min'] = min(range_val['min'], float(r_info['min']))
        range_val['max'] = max(range_val['max'], float(r_info['max']))


# Alternative Interpolation Approach via idw (not validated yet since results from nni are sufficient)
# Performance Advantage: Reduces computational speed up to 80% compared with nni (above)
# Drawback: Results are not as smooth and more point-like than nni
# for rast in tqdm(gs.list_grouped('raster', pattern='temp_*')[mapset]):
#     gs.mapcalc(f"{'{}_int'.format(rast)} = int({rast}*10)", overwrite=True)
#     gs.run_command('r.surf.idw', input='{}_int'.format(rast), output='{}_interpolated'.format(rast), overwrite=True, npoints=10)
#     gs.run_command('r.colors', map='{}_interpolated'.format(rast), color='celsius', overwrite=True)
#     gs.mapcalc(f"{'{}_interpolated'.format(rast)} = float({'{}_interpolated'.format(rast)})/10", overwrite=True)


# Requires prior preperation of input data (extending along borders) -> unique cross-val-fun possible?
# Calculating nni interpolation
r_surfnnbathy = Module("r.surf.nnbathy", run_=False)
r_mapcalc = Module("r.mapcalc", run_=False)

queue = ParallelModuleQueue(nprocs)

for temp_layer in temp_layers:
    interpolation = r_surfnnbathy(input=rast, output='{}_interpolated'.format(rast))
    masking = r_mapcalc(expression=f"{rast}_interpolated = if(borders_germany, {rast}_interpolated)")
    process_chain = MultiModule(module_list=[interpolation, masking])
    queue.put(process_chain)

queue.wait()
queue.get_num_run_procs()

for process in queue.get_finished_modules():
    print(process.returncode)