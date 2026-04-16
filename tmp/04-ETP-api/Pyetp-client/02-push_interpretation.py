import asyncio
from pyetp.client import connect
from pyetp.config import SETTINGS
from pyetp.uri import DataspaceURI


import xtgeo

SETTINGS.etp_url = "ws://localhost:9100"
SETTINGS.dataspace = "demo/PyETP"
SETTINGS.application_name = 'etpTest'
SETTINGS.application_version = '0.0.1'

token = ""


## Experimental test to see how we can push a full RESQML containing multiple objects at once. 
## this is not yet supported by PyETP
async def put_epc_model():
    async with connect(timeout=60) as client:
        await client.put_epc_mesh('../03-REST-api/data/Volve_Demo_Reservoir_Grid_Depth.epc', client.default_dataspace_uri)

## Reading a grid file using xtgeo and pushing it to the dataspace using PyETP client
async def put_xtgeo_grid():
    async with connect(timeout=60) as client:
        surf = xtgeo.surface_from_file('./data/test.gri', fformat='irap_binary')
        epsg_code = 23031
        urls = await client.put_xtgeo_surface(surf, epsg_code, client.default_dataspace_uri) # chunked upload using subarray if size too large
        for i in urls:
            print(i.raw_uri)

asyncio.run(put_xtgeo_grid())

