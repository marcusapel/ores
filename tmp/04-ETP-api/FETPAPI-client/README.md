# FETPAPI

FETPAPI is a SDK that enable developers to construct ETP (Energistics Transfer Protocol) v1.2 aware software applications.

The main target of FETPAPI is [the OSDU RDDMS](https://community.opengroup.org/osdu/platform/domain-data-mgmt-services/reservoir/open-etp-server) since the latter uses ETP1.2 as its main web API.

FETPAPI is developed in C++ with SWIG wrappers in Java, C# and Python which allows to be utilized with applications developed in one of these 4 programming languages. It also supports Windows (64 bits), Linux (glibc>=2.28).

FETPAPI is generally used with FESAPI which is a SDK that enable developers to easily deal with [RESQML2](https://docs.energistics.org/#RESQML/RESQML_TOPICS/RESQML-000-000-titlepage.html) (and other Energistics formats) data. RESQML2 is the format which is used to encode dataobjects in ETP1.2 messages.

# How to install
## Python
```
pip install fesapi fetpapi
```
## Other programming languages
You can either :
- build your binaries by yourself using CMake and instructions on [Github](https://github.com/F2I-Consulting/fetpapi/blob/main/README.md)
- or download the binaries for your own platform on [Github Release Assets]([Github](https://github.com/F2I-Consulting/fetpapi/blob/main/README.md)) (please [raise an issue](https://github.com/F2I-Consulting/fetpapi/issues) if your dedicated platform binaries is not present yet)

# How to start
## Python Examples
- A [Jupyter notebook file](https://github.com/F2I-Consulting/fetpapi/blob/main/python/example/fetpapi.ipynb) illustrating how to list dataspaces and dataspace content.
- An [ETP client](https://github.com/F2I-Consulting/fetpapi/blob/main/python/example/etp_client_example.py) which connects to an ETP server, lists the content of a dataspace, gets all dataobjects from it and finally shows some of the contents of an IJK Grid Representation and a Grid 2d Representation if present.
## C++ Examples
A [CLI ETP client](https://github.com/F2I-Consulting/fetpapi/blob/main/example/withFesapi/etpClient.cpp) illustrating several key functionalities.
## Java Examples
An [ETP client](https://github.com/F2I-Consulting/fetpapi/blob/main/cmake/FetpapiClientUsingFesapi.java) which connects to an ETP server, lists the content of a dataspace, gets all dataobjects from it and finally shows some of the contents of an IJK Grid Representation if present.
## C# Examples
Not available yet...
## Documentation
Doxygen documentation is auto generated and uploaded for [FETPAPI](https://f2i-consulting.com/fetpapi/doxygen/) and for [FESAPI](https://f2i-consulting.com/fesapi/doxygen/).
