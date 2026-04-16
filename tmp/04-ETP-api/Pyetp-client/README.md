# PyETP

PyETP is a young OpenSource repository contributed by Equinor.

This library is under active development and subject to breaking changes.

It originally started as an OpenSource Python ETP client to leverage RESQPY project that does not have support for a REST API nor a Energistics Transfers protocol (ETP).

This python module an also as some utility function to use the xtgeo open source project: https://github.com/equinor/xtgeo

RESQPY issue:
- [resqpy #680: REST API interface](https://github.com/bp/resqpy/issues/680)


# How to start

Installing pyetp
```
pip install pyetp
```

# How to use

The code snippets from this file will require you to run the commands from inside the `04-ETP-api/Pyetp-client` directory in a PowerShell terminal:
  ```powershell
  cd 04-ETP-api/Pyetp-client
  ```

In this folder you will find few Python script demonstrating some of the capabilities of the PyETP project.

You can open each of the .py file to better understand PyETP.

Note that it is not an exhaustive tutorial of PyETP, I am only highting few features.


1. Creating a new DataSpace
    ```powershell
    py 01-create_new_dataspace.py
    ```

    Checks if the dataspace is created using the OSDU OpenETP Client
    ```powershell
    docker run --rm  --network=host --entrypoint=sh osdu-etp-client -c '/bin/openETPServer space --server-url ws://localhost:9100/ --list'
    ```

2. Create a new grid and push it to the dataspace
    ```powershell
    py 02-push_interpretation.py
    ```

    Check all `resqml20.Grid2dRepresentation` in the dataspace
    ```
    docker run --rm  --network=host --entrypoint=sh osdu-etp-client -c '/bin/openETPServer space --server-url ws://localhost:9100/ -s demo/PyETP --toc --type resqml20.Grid2dRepresentation'
    ```

3. Use PyETP to read Grid2dRepresentation details
    ```powershell
    py 03-get_info.py "eml:///dataspace('demo/PyETP')/resqml20.Grid2dRepresentation(YOUR-UID)"
    ```

4. Delete the 'demo/PyETP' dataspace
    ```powershell
    py 04-delete_new_dataspace.py
    ```


# More examples for PyETP

Please refers to examples in the PytETP github repository: https://github.com/equinor/pyetp

# Notes: about this ETPClient

While working on this tutorial I have noticed few missing elements in the PyETP ETPClient.

- ETPClient cannot load all data from EPC file at once

- ETPClient is missing an API to list all object of a given RESQML type.

- The xtgeo loader in the ETPClient calls does not seems to set a name for the horizon.

