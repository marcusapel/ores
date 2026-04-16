# General

The code snippets from this file will require you to run the commands from inside the `02-ETP-OSDU-etp-client` directory in a PowerShell terminal:
  ```powershell
  cd 02-ETP-OSDU-etp-client
  ```

The Open ETP Server executable can also function as an ETP client. The same executable can operate in two different modes:
- **As a Server**: Use the `server` mode
  ```
  openETPServer server --help
  ```
- **As a Client**: Use the `space` mode
  ```
  openETPServer space --help
  ```

# Getting the OSDU ETP Client

Since the executable can act as both a server and a client, you can use the same Docker image that we pulled in step `01-Start-local-rddms`

## Reusing the Server Docker Image

The Docker container image, which was pulled during `docker compose up`, can also be used to run the client. There is no real need to pull a separate image.
```powershell
docker pull community.opengroup.org:5555/osdu/platform/domain-data-mgmt-services/reservoir/open-etp-server/open-etp-server-main:latest
```

For ease of use, tag the Docker image with a simpler name:
```powershell
docker tag community.opengroup.org:5555/osdu/platform/domain-data-mgmt-services/reservoir/open-etp-server/open-etp-server-main osdu-etp-client
```
This tags the latest image as `osdu-etp-client`, simplifying the remaining commands in this chapter.

## In Case of a Secured Connection.

YOU CAN SKIP HIS CHAPTER IF YOU ARE RUNNING RRDMS FROM YOUR NON SECURED CONNECTION.

If your ETP server is exposed via a secured connection (e.g., behind a secure WebSocket endpoint `wss://` instead of `ws://`), you will need a special build of the client that supports SSL.

Fortunately, OSDU provides a pre-compiled Docker container for the ETP client with SSL support, so you don’t need to build it from the source code.

To obtain this Docker container, pull it using the following command:
```powershell
docker pull community.opengroup.org:5555/osdu/platform/domain-data-mgmt-services/reservoir/open-etp-server/open-etp-sslclient-main
```

For ease of use, tag the Docker image with a simpler name:
```powershell
docker tag community.opengroup.org:5555/osdu/platform/domain-data-mgmt-services/reservoir/open-etp-server/open-etp-sslclient-main osdu-etp-sslclient
```
Then, use the `osdu-etp-sslclient` tag instead of `osdu-etp-client` in the examples below.

## Versioning

If you need a specific version of the OSDU ETP server and client instead of the latest image from the main branch, you can replace `-main` in the image name with the desired release branch name, such as `-release-0-25`, `-release-0-26`, etc.


To find all available image tags for the Open ETP Server, visit the OSDU Container Registry:

You can search all image tags of the Open ETP Server from the OSDU Container registry
- [OSDU Container Registry - Open ETP Server](https://community.opengroup.org/osdu/platform/domain-data-mgmt-services/reservoir/open-etp-server/container_registry/)

## Test the OSDU ETP Client

To test the image, use the following commands:

1. **Get Client Help**: Use the `--help` command
    ```powershell
    docker run --rm --network=host --entrypoint=sh osdu-etp-client -c '/bin/openETPServer space --help'
    ```
2. **List All Available Dataspaces**: Use the `--list` command.
    ```powershell
    docker run --rm  --network=host --entrypoint=sh osdu-etp-client -c '/bin/openETPServer space --server-url ws://localhost:9100/ --list '
    ```

## Pushing Data to the RDDMS

1. **Create a New Dataspace**:

    To create a new dataspace named `demo/Volve`, use the `--new` command:

    ```powershell
    docker run --rm --network=host --entrypoint=sh osdu-etp-client -c '/bin/openETPServer space -S ws://localhost:9100 --new -s demo/Volve'

    docker run --rm --network=host --entrypoint=sh osdu-etp-client -c '/bin/openETPServer space -S ws://localhost:9100/ --list'
    ```

2. **Import Initial Data**:
    
    To import initial data from a RESQML file, use the `--import-epc` command. Make sure the data file is in the data directory:

    ```powershell
    docker run --rm -v .\data:/data --network=host --entrypoint=sh osdu-etp-client -c '/bin/openETPServer space -S ws://localhost:9100/ -s demo/Volve --import-epc /data/Volve_Demo_Horizons_Depth.epc'
    ```

3. **Check Dataspace Contents**:

    To verify that the demo/Volve dataspace contains data, use the `--stats` command:

    ```powershell
    docker run --rm --network=host --entrypoint=sh osdu-etp-client -c '/bin/openETPServer space -S ws://localhost:9100/ -s demo/Volve --stats'
    ```

 ## Cleanup

To delete the `demo/Volve` dataspace, use the `--delete` command:
 ```powershell
 docker run --rm --network=host --entrypoint=sh osdu-etp-client -c '/bin/openETPServer space -S ws://localhost:9100/ -s demo/Volve --delete'
 ```


## Fast Track

The following chapters (`03-REST-api` and others) will require the `demo/Volve` dataset to be created with some data in it.

For convenience, we’ve added two PowerShell scripts that can automatically run commands from this chapter:

- **Setup**: 

  Run `quick_setup.ps1` to execute all the commands, from tagging the Open ETP client to loading the dataset into the `demo/Volve` dataspace.
  ```powershell
  quick_setup.ps1
  ```
  
- **Cleanup**: 

  Run `quick_cleanup.ps1` to delete the `demo/Volve` dataspace when you're finished.

  ```powershell
  quick_cleanup.ps1
  ```




