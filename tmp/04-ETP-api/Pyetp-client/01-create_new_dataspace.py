import asyncio
from pyetp.client import connect
from pyetp.config import SETTINGS
from pyetp.uri import DataspaceURI

SETTINGS.etp_url = "ws://localhost:9100"
SETTINGS.dataspace = "demo/PyETP"
SETTINGS.application_name = 'etpTest'
SETTINGS.application_version = '0.0.1'

token = ""

async def create_dataspace():
    async with connect(timeout=60) as client:
        # Create new dataspace if it doesn't exist
        await client.put_dataspaces_no_raise(client.default_dataspace_uri)

# To run the async function
asyncio.run(create_dataspace())
