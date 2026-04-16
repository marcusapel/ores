# Introduction
The full stack of the RDDMS is based on the public, non-proprietary [RESQML data standards](https://energistics.org/resqml-data-standards).

Several open-source projects utilize RESQML data standards:
- [OSDU OpenETP Server](https://community.opengroup.org/osdu/platform/domain-data-mgmt-services/reservoir/open-etp-server): The open-source implementation of the OSDU Reservoir Domain Data Management Services (Reservoir DDMS), which is one of the backend services and a part of the Open Subsurface Data Universe (OSDU) software ecosystem. OpenETPServer is a single, containerized service written in C++ that stores reservoir data in the RESQML format inside a PostgreSQL database.
- [OSDU Open ETP Client](https://community.opengroup.org/osdu/platform/domain-data-mgmt-services/reservoir/open-etp-client): Open-source implementation of a TypeScript ETP Client, also used to expose a REST API to the OSDU Reservoir Domain Data Management Services (Reservoir DDMS).
- [FESAPI](https://github.com/F2I-Consulting/fesapi): This project provides C++ classes that allow easy access for importing and exporting to the Energistics™ standards, especially RESQML™, and partly WITSML™ and PRODML™. This project includes SWIG wrappers to expose the C++ classes to .NET, Java, and Python applications.
- [FETPAPI](https://github.com/F2I-Consulting/fetpapi): This project provides C++ classes that allow easy access for pushing and pulling messages using the Energistics Transfer Protocol (ETP™) version 1.2. This project includes SWIG wrappers to expose the C++ classes to .NET, Java, and Python applications.
- [RESQPY](https://github.com/bp/resqpy): resqpy is a pure Python package that provides a programming interface (API) for reading, writing, and modifying reservoir models in the RESQML format.
- [PyEtp](https://github.com/equinor/pyetp): An initiative to provide a Python ETP client.

# What is ETP

The Energistics Transfer Protocol (ETP) is a communication protocol designed for the efficient transfer of data between applications in the energy industry. It is developed by Energistics, a global consortium that creates open data exchange standards for the oil and gas industry.

ETP facilitates real-time data exchange and supports various data types, including well logs, reservoir data, and production data.

Key features of ETP include:

- **Real-time Data Transfer**: Enables the streaming of data in real-time between systems.
- **Interoperability**: Ensures compatibility between different software applications and systems.
- **Efficiency**: Optimizes data transfer to reduce latency and improve performance.
- **Standardization**: Provides a standardized way to exchange data, reducing the need for custom integration solutions.

ETP is part of the broader suite of Energistics standards, which also includes RESQML for reservoir models, WITSML for well data, and PRODML for production data.

# Content

We have already covered one open-source ETP client in chapter [02-OSDU-etp-client](../02-OSDU-etp-client).<br>
We used it to load initial data in RESQML format to the RDDMS as well as a powerful administration tool to query details on the contents of the RDDMS.

In this section, we have prepared a short introduction to two other open-source ETP clients:
- [FETPAPI](FETPAPI-client/README.md) <p> Open-source ETP client in Python, C++, Java and C# from F2I-Consulting
- [Pyetp](Pyetp-client/README.md) <p> Open-source ETP client in Python from BP
