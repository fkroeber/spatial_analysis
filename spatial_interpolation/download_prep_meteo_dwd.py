### --- To run on shell --- ###
# working_dir='/home/felix/Desktop/assigment_interpolation'
# export working_dir

# Setup/Imports
import glob
import numpy as np
import pandas as pd
import os
import requests
import wget
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from pyproj import Transformer

pd.set_option('display.float_format', lambda x: '%.2f' % x)

# Download temperature data (station/point measurements, hourly)
# For each station a txt file is available storing data for a certain station & period mid-2020 up to yesterday
baseurl = 'https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/hourly/air_temperature/recent/'
download_dir = os.path.join(os.environ['working_dir'], 'temp_raw_data')

page_temperature = requests.get(baseurl)
page_temperature = BeautifulSoup(page_temperature.content, "html.parser")

import zipfile as zipfile
for href in page_temperature.find_all('a', string=lambda text: 'stundenwerte' in text.split('_')):
    wget.download(os.path.join(baseurl, href.text.strip()), download_dir)
    zip = zipfile.ZipFile(os.path.join(download_dir, href.text.strip()), 'r')
    zip.extractall(download_dir)
    zip.close()
    os.remove(os.path.join(download_dir, href.text.strip()))

wget.download(os.path.join(baseurl, 'DESCRIPTION_obsgermany_climate_hourly_tu_recent_en.pdf'), download_dir)
wget.download(os.path.join(baseurl, 'TU_Stundenwerte_Beschreibung_Stationen.txt'), download_dir)

# Parse station geodata (station_id, lat/lon, name,...)
stations = pd.read_fwf(os.path.join(download_dir, 'TU_Stundenwerte_Beschreibung_Stationen.txt'), encoding='latin1', header=0, widths=[5,9,9,15,12,10,41,25]).iloc[1:,:]
stations_columnnames = pd.read_csv(os.path.join(download_dir,'TU_Stundenwerte_Beschreibung_Stationen.txt'), delim_whitespace=True, skipinitialspace=True, encoding='latin1', nrows=0).columns.to_list()
stations.columns = stations_columnnames
stations_columnnames = stations.columns.to_list()
dtypes = ['int64','int64','int64','int64','float64','float64','object', 'object']
del zip
stations = stations.astype(dict(zip(stations_columnnames, dtypes)))
stations.describe(include='all')

# Get temperature data for all dates of interest
# Define daterange of interest and select all hours on the first day of each selected month
daterange = pd.date_range(start='2020-06-01', end='2021-05-01', freq='MS')
dates = [pd.date_range(start=day, end=day+pd.Timedelta("1 day"), freq='H', closed='left') for day in daterange]
dates = [date_hour for date in dates for date_hour in date]
dates = [int(datetime.strftime(day_hour, "%Y%m%d%H")) for day_hour in dates]

# Filter each station data file for selected dates and store data for all stations together in dict
station_data_all = {}
for i, station_file in enumerate(glob.glob(os.path.join(download_dir,'produkt_tu_stunde*.txt'))):
    station_data = pd.read_csv(station_file, sep=';', usecols=[0,1,3], names=['station_id', 'time_hour', 'temp'], header=0)
    station_data_all[f'station_data_{i}'] = station_data[station_data['time_hour'].isin(dates)]

# Merge with station data to get georeferenced temperature data
station_data_all = pd.concat(station_data_all).merge(stations.iloc[:,[0,3,4,5,6,7]], left_on='station_id', right_on='Stations_id')
del station_data_all['Stations_id']
station_data_all.replace(-999, np.nan).describe(include='all')

# Reproject data to UTM32N
transformer = Transformer.from_crs('epsg:4326', 'epsg:32632')
station_data_all['coord_x'] = list(map(lambda x,y: transformer.transform(x, y)[0], station_data_all.geoBreite, station_data_all.geoLaenge))
station_data_all['coord_y'] = list(map(lambda x,y: transformer.transform(x, y)[1], station_data_all.geoBreite, station_data_all.geoLaenge))

# Export data for each timestamp as csv for further use
export_dir = os.path.join(os.environ['working_dir'], 'temp_prep_data')
for name, group in station_data_all.groupby('time_hour'):
    group.to_csv(export_dir+os.path.sep +'temp'+str(name)+'.csv', sep = '|', na_rep='NULL')