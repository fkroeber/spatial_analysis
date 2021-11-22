import streamlit as st
import io
import math
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import zipfile
from datetime import datetime
from PIL import Image

# Define app layout
st.set_page_config(layout="wide")
st.title('IDW Interpolation - Hourly Temperature (Germany)')
st.text('')
col1,_,col2,_,col3 = st.columns([4,1,1,0.5,4])

# Load data
@st.experimental_singleton
def load_image_data():
    idw_img_url = "https://dl.dropboxusercontent.com/s/10v4yoaig1ed7u0/interpol_results.zip?dl=0"
    resp = requests.get(idw_img_url, stream=True)
    zip_file = zipfile.ZipFile(io.BytesIO(resp.content))
    unzipped_files = [zip_file.open(f) for f in zip_file.infolist()]
    layers_out = []
    for f in unzipped_files:
        layer = Image.open(f)
        layer.filename = f.name
        layers_out.append(layer) 
    return layers_out

@st.experimental_singleton
def load_stat_data():
    idw_stat_url = "https://dl.dropboxusercontent.com/s/77r3hwmlipak5l0/interpolation_stats.csv?dl=0"
    return pd.read_csv(idw_stat_url)

@st.experimental_singleton
def load_val_data():
    idw_val_url = "https://dl.dropboxusercontent.com/s/bm26d038xr925sh/interpolation_validation.csv?dl=0"
    return pd.read_csv(idw_val_url)

with st.spinner('Application is loading...'):
    idw_imgs = load_image_data()
    idw_stats = load_stat_data()
    idw_val = load_val_data()

# Display interpolated map
with col1:
    date_time = st.selectbox('Date & Time', ('01/07/2020 13:00', '01/02/2021 20:00'))
    date_time = datetime.strptime(date_time, "%d/%m/%Y %H:%M").strftime("%Y%m%d%H")
    power = st.slider('Exponential Distance Weight', min_value=0.5, max_value=2.75, value=2.0, step=0.25)
    power = str(power).replace(".","")
    npoints = st.slider('Number of Points', min_value=1, max_value=19, value=15, step=2)

    selected_img_name = "temp{}_idw_pow{}_npoi{}".format(date_time, power, npoints)
    selected_img = [img for img in idw_imgs if img.filename == "{}.png".format(selected_img_name)][0]
    st.image(selected_img)

# Display stats panel & validation results
idw_stats_selected = idw_stats[idw_stats['name']==selected_img_name]
with col2:
    st.subheader('Basic stats')
    st.metric('min', round(idw_stats_selected['min'].values[0],2),delta=None)
    st.metric('max', round(idw_stats_selected['max'].values[0],2),delta=None)
    st.metric('sd', round(idw_stats_selected['sd'].values[0],2),delta=None)
    st.metric("Moran's I", round(idw_stats_selected['moran'].values[0],2),delta=None)
    st.metric("Entropy", round(idw_stats_selected['entropy_mean'].values[0],2),delta=None)

# Display validation results
with col3:
    st.subheader('Validation results')
    st.text('''The following figure displays the root mean square error 
for all possible combinations of the parameters weigth and number of points 
for the selected date. The black dot marks the current parameter selection.''')

    with st.container():
        def rmse(single_diffs):
            return math.sqrt((single_diffs**2).sum()/single_diffs.count())

        def get_pow(layer_name):
            str_pow = [name.rsplit("_",2)[1].replace("pow","") for name in layer_name]
            float_pow = [float(pow[:1]+'.'+pow[1:]) if len(pow)>1 else float(pow) for pow in str_pow]
            return float_pow

        def get_npoi(layer_name):
            int_npoi = [int(name.rsplit("_",1)[1].replace("npoi","")) for name in layer_name]
            return int_npoi


        val_overview = (idw_val.groupby(['idw_layer'])
                            .agg(rmse=('diff_meas_interpol', rmse))
                            .reset_index()
                            .assign(temptime = lambda x: [name.split("_")[0] for name in x['idw_layer']])
                            .assign(npoi=lambda x: get_npoi(x['idw_layer']))
                            .assign(pow=lambda x: get_pow(x['idw_layer'])))

        fig_prep = (val_overview[val_overview['temptime']==selected_img_name.split("_")[0]]
                            .sort_values(['npoi','pow'])
                            .groupby(['npoi'])
                            .agg({'pow': lambda x: list(x),
                                    'rmse': lambda x: list(x)})
                            .reset_index())

        fig = go.Figure(data=go.Heatmap(
            x = fig_prep['pow'][0],
            y = list(fig_prep['npoi']),
            z = list(fig_prep['rmse']),
            zsmooth='best',
            colorscale='RdBu_r',
            hoverinfo=None,
            name=''))
        fig.add_scatter(x=val_overview[val_overview['idw_layer']==selected_img_name]['pow'].tolist(),
                        y=val_overview[val_overview['idw_layer']==selected_img_name]['npoi'].tolist(),
                        mode='markers',
                        name='current parameter selection',
                        marker=dict(size=12, color='black'))
        fig.update_xaxes(title=dict(text='power'))
        fig.update_yaxes(title=dict(text='number of points'))
        st.plotly_chart(fig)


        st.text('')
        st.text('''Below the leave-one-group-out cross-validation results for the chosen parameter
combination is shown. Points are colorised according to their height above see level
as this variable explains the strongest deviations.''')
        val_specific = idw_val[idw_val['idw_layer']==selected_img_name]
        val_specific['temp_interpolated'] = val_specific['temp'] + val_specific['diff_meas_interpol']
        fig = px.scatter(val_specific, 
                        x='temp', 
                        y='temp_interpolated', 
                        color='height', 
                        color_continuous_scale=["#1A9850", "#91CF60", "#F1A340", "#8C510A"],
                        trendline='ols',
                        trendline_color_override='green',
                        hover_data=['station_name', 'federal_state'],
                        labels=dict(temp='measured temperature(°C)', temp_interpolated='interpolated temperature(°C)'))
        fig.add_trace(go.Scatter(x=np.arange(min(val_specific['temp']), max(val_specific['temp']),0.01),
                                y=np.arange(min(val_specific['temp']), max(val_specific['temp']),0.01),
                                text='1:1 line',
                                name='',
                                mode='lines',
                                line=dict(color='grey'),
                                showlegend=False))
        st.plotly_chart(fig)