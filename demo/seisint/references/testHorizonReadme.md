# Example of Seismic Horizon in Resqml22 JSON

This file describe the content of the zip showing different options to store a seismic horizon in RESQML2.2 using a JSON representation file.

Different options are shown in the zip file, all displaying horizon defined as a set of Z values on top of a regular grid of X and Y values. But, the same flexibility of the Resqml data model applies to arbitrary set of points (with no XY structures), 2d lines. It also applies to faults and includes triangles, polyhedra, etc.

## Horizon with inline X, Y and Z Values

The first file testHorizonEverythingIncluded.json contains an array of objects, the first object is the horizon itself, and the other ones are the objects this horizons may be referencing. Note that these objects (Coordinate Reference System, Interpretation and Features) are links so they are typically sent one time, then horizons can attach to them. Besides the coordinate system information, everything in references is optional.

The horizon itself is a simple one, with a single patch. The patch is defined using two vectors for X and Y coordinates.
```json
"SupportingGeometry": {
    "$type": "resqml22.Point3dLatticeArray",
    "AllDimensionsAreOrthogonal": true,
    "Dimension": [
    {
        "$type": "resqml22.Point3dLatticeDimension",
        "Direction": {
            "$type": "resqml22.Point3d",
            "Coordinate1": 0,
            "Coordinate2": 0,
            "Coordinate3": 1
        },
        "Spacing": {
            "$type": "eml23.FloatingPointConstantArray",
            "Value": 200,
            "Count": 1
        }
    },
    {
        "$type": "resqml22.Point3dLatticeDimension",
        "Direction": {
            "$type": "resqml22.Point3d",
            "Coordinate1": 0,
            "Coordinate2": 1,
            "Coordinate3": 0
        },
        "Spacing": {
            "$type": "eml23.FloatingPointConstantArray",
            "Value": 250,
            "Count": 3
        }
    }
    ],
    "Origin": {
        "$type": "resqml22.Point3d",
        "Coordinate1": 5010,
        "Coordinate2": 6020,
        "Coordinate3": 0
    }
}
```


 and an inline JSON array. 

```json
"ZValues": {
    "$type": "eml23.FloatingPointXmlArray",
    "CountPerValue": 1,
    "Values": [ 300.0, 310.0, 350.0, 355.0, 400.0, 410.0, 450.0, 455.0 ]
}
```

## Horizon referencing a binGrid

The second file testHorizonReferencingBinGrid.json shows a horizon that references an external grid. Unlike the previous geometry where the X and Y are vector based, this defines the supporting geometry X an Y coordinates as a window inside an existing BinGrid. It contains just the horizon.

```json
"SupportingGeometry": {
    "$type": "resqml22.Point3dFromRepresentationLatticeArray",
    "NodeIndicesOnSupportingRepresentation": {
        "$type": "eml23.IntegerLatticeArray",
        "StartValue": 0,
        "Offset": [
        {
            "$type": "eml23.IntegerConstantArray",
            "Value": 1,
            "Count": 1
        },
        {
            "$type": "eml23.IntegerConstantArray",
            "Value": 1,
            "Count": 3
        }
        ]
    },
    "SupportingRepresentation": {
        "$type": "eml23.DataObjectReference",
        "Uuid": "aa5b90f1-2eab-4fa6-8720-69dd4fd51a4d",
        "QualifiedType": "resqml22.Grid2dRepresentation",
        "Title": "Seismic BinGrid"
    }
}
```

## Horizon referencing data inside an Excel spreadsheet

In order to demonstrate how we can reuse an existing file, the third file testHorizonReferencingXL.json shows a horizon that references an external Excel file. The Z coordinates are defined using a given column in a given sheet of a spreadsheet.
I also included the Excel file in the zip, so you can see how the data is referenced.

Other file types can be used, such as CSV, SEGY but the principle is the same, we just need to define the rule of how to extract the data from the file (e.g sheet name, column and row indices, etc.)

```json
"ZValues": {
    "$type": "eml23.FloatingPointExternalArray",
    "ArrayFloatingPointType": "arrayOfFloat32LE",
    "CountPerValue": 1,
    "Values": {
        "$type": "eml23.ExternalDataArray",
        "ExternalDataArrayPart": [ {
            "$type": "eml23.ExternalDataArrayPart",
            "Count": [ 8 ],
            "PathInExternalFile": "Sheet1",
            "StartIndex": [ 1, 2 ],
            "URI": "horizon.xlsx",
            "MimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        } ]
    }
}
```

## OSDU information

The first horizon also contains some information that is specific to the OSDU data platform. This information is stored in the "OSDUIntegration" part. Here is an example of some of the information that can be stored in this part:


```json
"OSDUIntegration": {
    "legalTags": "opendes-ReservoirDDMS-Legal-Tag",
    "OwnerGroup": [
      "group1"
    ],
    "OSDULineageAssertion": {
      "$type": "eml23.OSDULineageAssertion",
      "ID": "opendes:work-product-component--SeismicHorizon:65681972-6eef-497e-b1d8-2f54a87ad950",
      "LineageRelationshipKind": "direct"
    }
  }
  ```

  ## Schema

  You can see the full json schema for a 2D Grid in Grid2dRepresentation.json