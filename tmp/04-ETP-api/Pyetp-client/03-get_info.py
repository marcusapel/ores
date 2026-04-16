# Returns information about a data object in an ETP dataspace.
#
# This function takes one argument.
# The argument is the ETP URI that identifies the data object in a particular dataspace
# ex: eml:///dataspace('demo/PyETP')/resqml20.Grid2dRepresentation(...)
#
# You can use the URL return by the push_interpretation.py script

import asyncio
import sys
from pyetp.client import connect
from pyetp.config import SETTINGS
from pyetp.uri import DataspaceURI, DataObjectURI

SETTINGS.etp_url = "ws://localhost:9100"
SETTINGS.dataspace = "demo/PyETP"
SETTINGS.application_name = 'etpTest'
SETTINGS.application_version = '0.0.1'

token = ""

async def get_info(str):
    async with connect(timeout=60) as client:
        urls = await client.get_data_objects(str)   
        print(urls)


if len(sys.argv) == 1:
    first_arg = sys.argv[1]
    asyncio.run(
        get_info(first_arg)
        )
else:
    print("Please provide an argument")
