# -*- coding: utf-8 -*-
"""
/***************************************************************************
 OSMQuery
 A Python toolbox for ArcGIS
 OSM Overpass API frontend
                             -------------------
        begin                : 2018-08-20
        copyright            : (C) 2018 by Riccardo Klinger
        email                : riccardo.klinger at gmail dot com
        contributor          : Riccardo Klinger
 ***************************************************************************/
/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

import arcpy
import requests
import json
import time
import datetime
from os.path import dirname, join, abspath

# Constants for building the query to the Overpass API
QUERY_URL = "http://overpass-api.de/api/interpreter"
QUERY_START = "[out:json][timeout:25]"
QUERY_DATE = '[date:"timestamp"];('
QUERY_END = ');(._;>;);out;>;'

# Set some environment settings
arcpy.env.overwriteOutput = True
arcpy.env.addOutputsToMap = True


class Toolbox(object):
    def __init__(self):
        """Define the toolbox (the name of the toolbox is the name of the
        .pyt file)."""
        self.label = "OSM Query Toolbox"
        self.alias = "OSMQueryToolbox"

        # List of tool classes associated with this toolbox
        self.tools = [GetOSMDataSimple, GetOSMDataExpert]

    @classmethod
    def create_result_fc(cls, geometry_type, fields, timestamp):
        fc_name = '%ss_%s' % (geometry_type, str(timestamp))
        fc = join(arcpy.env.scratchWorkspace, fc_name)

        arcpy.AddMessage("\nCreating %s feature layer %s..." %
                         (geometry_type.lower(), fc_name))
        if geometry_type == 'Line':
            geometry_type = 'Polyline'
        arcpy.CreateFeatureclass_management(arcpy.env.scratchWorkspace,
                                            fc_name, geometry_type.upper(),
                                            "", "DISABLED", "DISABLED",
                                            arcpy.SpatialReference(4326), "")
        arcpy.AddMessage("\tAdding attribute OSM_ID...")
        arcpy.AddField_management(fc, "OSM_ID", "DOUBLE", 12, 0, "", "OSM_ID")
        arcpy.AddMessage("\tAdding attribute DATETIME...")
        arcpy.AddField_management(fc, "DATETIME", "DATE", "", "", "",
                                  "DateTime")
        for field in fields:
            try:
                field = field.replace(":", "")
                arcpy.AddMessage("\tAdding attribute %s..." % field)
                arcpy.AddField_management(fc, field, "STRING", 255, "", "",
                                          field, "NULLABLE")
            except arcpy.ExecuteError:
                arcpy.AddMessage("\tAdding attribute %s failed.")
        return fc

    @classmethod
    def extract_features_from_json(cls, data):
        """Extract lists of point, line, polygon objects from an Overpass API
        response JSON object"""
        points = [e for e in data['elements'] if e["type"] == "node"]
        lines = [e for e in data['elements'] if e["type"] == "way" and
                 (e["nodes"][0] != e["nodes"][len(e["nodes"])-1])]
        polygons = [e for e in data['elements'] if e["type"] == "way" and
                    (e["nodes"][0] == e["nodes"][len(e["nodes"])-1])]
        return points, lines, polygons

    @classmethod
    def get_attributes_from_features(cls, points, lines, polygons):
        point_fc_fields = set()
        line_fc_fields = set()
        polygon_fc_fields = set()
        for element in [e for e in points if "tags" in e]:
            for tag in element["tags"]:
                point_fc_fields.add(tag)
        for element in [e for e in lines if "tags" in e]:
            for tag in element["tags"]:
                line_fc_fields.add(tag)
        for element in [e for e in polygons if "tags" in e]:
            for tag in element["tags"]:
                polygon_fc_fields.add(tag)
        return point_fc_fields, line_fc_fields, polygon_fc_fields

    @classmethod
    def fill_feature_classes(cls, data, requesttime):
        fcs = [None, None, None]

        # ------------------------------------------------------
        # Creating feature classes according to the response
        # ------------------------------------------------------

        # Extract geometries (if present) from JSON data: points (nodes),
        # lines (open ways; i.e. start and end node are not identical) and
        # polygons (closed ways)
        points, lines, polygons = Toolbox.extract_features_from_json(data)

        # Per geometry type, gather all atributes present in the data
        # through elements per geometry type and collect their attributes
        point_fc_fields, line_fc_fields, polygon_fc_fields = \
            Toolbox.get_attributes_from_features(points, lines, polygons)

        # Per geometry type, create a feature class if there are features in
        # the data
        timestamp = int(time.time())
        if len(points) > 0:
            point_fc = Toolbox.create_result_fc('Point', point_fc_fields,
                                                timestamp)
            point_fc_cursor = arcpy.InsertCursor(point_fc)
            fcs[0] = point_fc
        else:
            arcpy.AddMessage("\nData contains no point features.")

        if len(lines) > 0:
            line_fc = Toolbox.create_result_fc('Line', line_fc_fields,
                                               timestamp)
            line_fc_cursor = arcpy.InsertCursor(line_fc)
            fcs[1] = line_fc
        else:
            arcpy.AddMessage("\nData contains no line features.")

        if len(polygons) > 0:
            polygon_fc = Toolbox.create_result_fc('Polygon', polygon_fc_fields,
                                               timestamp)
            polygon_fc_cursor = arcpy.InsertCursor(polygon_fc)
            fcs[2] = polygon_fc
        else:
            arcpy.AddMessage("\nData contains no polygon features.")

        # ------------------------------------------------------
        # Filling feature classes according to the response
        # ------------------------------------------------------

        for element in data['elements']:
            # Deal with nodes first
            try:
                if element["type"] == "node" and "tags" in element:
                    row = point_fc_cursor.newRow()
                    point_geometry = \
                        arcpy.PointGeometry(arcpy.Point(element["lon"],
                                                        element["lat"]),
                                            arcpy.SpatialReference(4326))
                    row.setValue("SHAPE", point_geometry)
                    row.setValue("OSM_ID", element["id"])
                    row.setValue("DATETIME", requesttime)
                    for tag in element["tags"]:
                        try:
                            row.setValue(tag.replace(":", ""),
                                         element["tags"][tag])
                        except:
                            arcpy.AddMessage("Adding value failed.")
                    point_fc_cursor.insertRow(row)
                    del row
                if element["type"] == "way" and "tags" in element:
                    # Get needed node geometries:
                    nodes = element["nodes"]
                    node_geometry = []
                    # Find nodes in reverse mode
                    for node in nodes:
                        for NodeElement in data['elements']:
                            if NodeElement["id"] == node:
                                node_geometry.append(
                                        arcpy.Point(NodeElement["lon"],
                                                    NodeElement["lat"]))
                                break
                    if nodes[0] == nodes[len(nodes) - 1]:
                        row = polygon_fc_cursor.newRow()
                        pointArray = arcpy.Array(node_geometry)
                        row.setValue("SHAPE", pointArray)
                        row.setValue("OSM_ID", element["id"])
                        row.setValue("DATETIME", requesttime)
                        # Now deal with the way tags:
                        if "tags" in element:
                            for tag in element["tags"]:
                                try:
                                    row.setValue(tag.replace(":", ""),
                                                 element["tags"][tag])
                                except:
                                    arcpy.AddMessage("Adding value failed.")
                        polygon_fc_cursor.insertRow(row)
                        del row
                    else:  # lines have different start end endnodes:
                        row = line_fc_cursor.newRow()
                        pointArray = arcpy.Array(node_geometry)
                        row.setValue("SHAPE", pointArray)
                        row.setValue("OSM_ID", element["id"])
                        row.setValue("DATETIME", requesttime)
                        # now deal with the way tags:
                        if "tags" in element:
                            for tag in element["tags"]:
                                try:
                                    row.setValue(tag.replace(":", ""),
                                                 element["tags"][tag])
                                except:
                                    arcpy.AddMessage("Adding value failed.")
                        line_fc_cursor.insertRow(row)
                        del row
            except:
                arcpy.AddWarning("OSM element %s could not be written to FC" %
                                 element["id"])
        if fcs[0]:
            del point_fc_cursor
        if fcs[1]:
            del line_fc_cursor
        if fcs[2]:
            del polygon_fc_cursor
        return fcs

    @classmethod
    def set_spatial_reference(cls, srs, transformation):
        """Given a Spatial Reference System string and (potentially) a
        transformation, create an arcpy.SpatialReference object and (if given)
        set the geographic transformation in the environment settings."""
        if srs is not None:
            spatial_reference = arcpy.SpatialReference()
            spatial_reference.loadFromString(srs)
        else:
            spatial_reference = arcpy.SpatialReference(4326)
        if transformation is not None:
            arcpy.env.geographicTransformations = transformation
        return spatial_reference

    @classmethod
    def get_bounding_box(cls, extent_indication_method, region_name, extent):
        """ Given a method for indicating the extent to be queried and either
        a region name or an extent object, construct the string with extent
        information for querying the Overpass API"""
        if extent_indication_method == "Define a bounding box":
            if extent.spatialReference == arcpy.SpatialReference(4326):
                # No reprojection necessary for EPSG:4326 coordinates
                bounding_box = [extent.YMin, extent.XMin, extent.YMax,
                                extent.XMax]
            else:
                # The coordinates of the extent object need to be reprojected
                # to EPSG:4326 for query building
                ll = arcpy.PointGeometry(arcpy.Point(extent.XMin, extent.YMin),
                                         extent.spatialReference).projectAs(
                        arcpy.SpatialReference(4326))
                ur = arcpy.PointGeometry(arcpy.Point(extent.XMax, extent.YMax),
                                         extent.spatialReference).projectAs(
                        arcpy.SpatialReference(4326))
                bounding_box = [ll.extent.YMin, ll.extent.XMin, ur.extent.YMax,
                                ur.extent.XMax]
            return '', '(%s);' % ','.join(str(e) for e in bounding_box)

        elif extent_indication_method == "Geocode a region name":
            # Get an area ID from Nominatim geocoding service
            nominatim_url = 'https://nominatim.openstreetmap.org/search?q=' \
                            '%s&format=json' % region_name
            arcpy.AddMessage("\nGecoding region using Nominatim: %s..." %
                             nominatim_url)
            nominatim_response = requests.get(nominatim_url)
            try:
                nominatim_data = nominatim_response.json()
                for result in nominatim_data:
                    if result["osm_type"] == "relation":
                        nominatim_area_id = result['osm_id']
                        try:
                            arcpy.AddMessage("\tFound region %s" %
                                             result['display_name'])
                        except:
                            arcpy.AddMessage("\tFound region %s" %
                                             nominatim_area_id)
                        break
                bounding_box_head = 'area(%s)->.searchArea;' % \
                                    (int(nominatim_area_id) + 3600000000)
                bounding_box_data = '(area.searchArea);'
                return bounding_box_head, bounding_box_data
            except:
                arcpy.AddError("\tNo region found!")
                return '', ''
        else:
            raise ValueError


class GetOSMDataSimple(object):
    def __init__(self):
        """Define the tool (tool name is the name of the class)."""
        self.label = "Get OSM Data"
        self.description = "Get OpenStreetMap data using relatively simple, " \
                           "pre-formulated queries to the Overpass API"
        self.canRunInBackground = False

    def get_config(self, config_item):
        """Load the configuration file and find either major OSM tag keys or
        suitable OSM tag values for a given key"""
        # Load JSON file with configuration info
        json_file = join(dirname(abspath(__file__)), 'config/tags.json')
        try:
            with open(json_file ) as f:
                config_json = json.load(f)
        except IOError:
            arcpy.AddError('Configuration file %s not found.' % json_file)
        except ValueError:
            arcpy.AddError('Configuration file %s is not valid JSON.' %
                           json_file)
        # Compile a list of all major OSM tag keys
        if config_item == "all":
            return [key for key in config_json]
        # Compile a list of all major OSM tag values for the given OSM tag key
        else:
            return [value for value in config_json[config_item]]

    def get_servers(self):
        """Load the configuration file and find Overpass API endpoints
        (this function is not in use yet)"""
        # Load JSON file with configuration info
        json_file = join(dirname(abspath(__file__)), 'config/servers.json')
        try:
            with open(json_file) as f:
                config_json = json.load(f)
        except IOError:
            arcpy.AddError('Configuration file %s not found.' % json_file)
        except ValueError:
            arcpy.AddError('Configuration file %s is not valid JSON.' %
                           json_file)
        return [server for server in config_json["overpass_servers"]]

    def getParameterInfo(self):
        """Define parameter definitions for the ArcGIS toolbox"""
        param0 = arcpy.Parameter(
                displayName="OSM tag key",
                name="in_tag",
                datatype="GPString",
                parameterType="Required",
                direction="Input")
        param0.filter.list = self.get_config('all')
        param0.value = param0.filter.list[0]
        param1 = arcpy.Parameter(
                displayName="OSM tag value",
                name="in_key",
                datatype="GPString",
                parameterType="Required",
                direction="Input",
                multiValue=True)
        param2 = arcpy.Parameter(
                displayName="Spatial extent indication method",
                name="in_regMode",
                datatype="GPString",
                parameterType="Required",
                direction="Input")
        param2.filter.list = ["Geocode a region name", "Define a bounding box"]
        param2.value = "Define a bounding box"
        param3 = arcpy.Parameter(
                displayName="Region name",
                name="in_region",
                datatype="GPString",
                parameterType="Optional",
                direction="Input")
        param4 = arcpy.Parameter(
                displayName="Bounding box",
                name="in_bbox",
                datatype="GPExtent",
                parameterType="Optional",
                direction="Input",
                enabled=False)
        param5 = arcpy.Parameter(
                displayName="Output CRS",
                name="in_crs",
                datatype="GPCoordinateSystem",
                parameterType="Optional",
                category="Adjust the CRS of the result data - default is "
                         "EPSG:4326 (WGS 1984):",
                direction="Input")
        param5.value = arcpy.SpatialReference(4326)
        param6 = arcpy.Parameter(
                displayName="Transformation",
                name="in_transformation",
                datatype="GPString",
                parameterType="Optional",
                category="Adjust the CRS of the result data - default is "
                         "EPSG:4326 (WGS 1984):",
                direction="Input",
                enabled=False)
        param7 = arcpy.Parameter(
                displayName="Reference date/time UTC",
                name="in_date",
                datatype="GPDate",
                parameterType="Optional",
                direction="Input")
        now = datetime.datetime.utcnow()
        param7.value = now.strftime("%d.%m.%Y %H:%M:%S")

        param_out0 = arcpy.Parameter(
                displayName="Layer containing OSM point data",
                name="out_nodes",
                datatype="GPFeatureLayer",
                parameterType="Derived",
                direction="Output")
        param_out1 = arcpy.Parameter(
                displayName="Layer containing OSM line data",
                name="out_ways",
                datatype="GPFeatureLayer",
                parameterType="Derived",
                direction="Output")
        param_out2 = arcpy.Parameter(
                displayName="Layer containing OSM polygon data",
                name="out_poly",
                datatype="GPFeatureLayer",
                parameterType="Derived",
                direction="Output")

        return [param0, param1, param2, param3, param4, param5, param6,
                param7, param_out0, param_out1, param_out2]

    def isLicensed(self):
        """Set whether tool is licensed to execute."""
        return True

    def updateParameters(self, parameters):
        """Modify the values and properties of parameters before internal
        validation is performed.  This method is called whenever a parameter
        has been changed."""

        # Update the parameters of keys accroding the values of "in_tag"
        parameters[1].filter.list = self.get_config(parameters[0].value)

        # Switch the availability of the 'region name' parameter and the
        # 'extent' parameter depending on which extent indication method is
        # selected
        if parameters[2].value == "Geocode a region name":
            parameters[3].enabled = True
            parameters[4].enabled = False
        else:
            parameters[3].enabled = False
            parameters[4].enabled = True

        if parameters[5].value is not None:
            target_sr = arcpy.SpatialReference()
            # target_sr.loadFromString(parameters[5].value).exportToString())
            target_sr.loadFromString(parameters[5].value)
            # If necessary, find candidate transformations between EPSG:4326
            # and <target_sr> and offer them in the dropdown menu
            if target_sr.factoryCode != 4326:
                parameters[6].enabled = True
                parameters[6].filter.list = \
                    arcpy.ListTransformations(arcpy.SpatialReference(4326),
                                              target_sr)
                parameters[6].value = parameters[6].filter.list[0]
            if target_sr.factoryCode == 4326:
                parameters[6].enabled = False
        return

    def updateMessages(self, parameters):
        """Modify the messages created by internal validation for each tool
        parameter. This method is called after internal validation."""
        # If only time is selected, year will be autofilled with "1899"
        if parameters[7].value.year < 2004:
            parameters[7].setWarningMessage("No or invalid date provided! "
                                            "Date must be greater than 9th "
                                            "of August 2004!")
        return

    def execute(self, parameters, messages):
        """The code that is run, when the ArcGIS tool is run."""

        query_date = QUERY_DATE.replace("timestamp",
                                        parameters[7].value.strftime("%Y-%m-%d"
                                                                     "T%H:%M:"
                                                                     "%SZ"))

        # Create the spatial reference and set the geographic transformation
        # in the environment settings (if given)
        sr = Toolbox.set_spatial_reference(parameters[5].value,
                                           parameters[6].value)

        # Get the bounding box-related parts of the Overpass API query, using
        # the indicated extent or by geocoding a region name given by the user
        bbox_head, bbox_data = Toolbox.get_bounding_box(parameters[2].value,
                                                        parameters[3].value,
                                                        parameters[4].value)

        # Get the list of OSM tag values checked by the user. The tool makes
        # the user supply at least one key.
        tag_key = parameters[0].value
        tag_values = parameters[1].value.exportToString().split(";")

        # If the wildcard (*) option is selected, replace any other tag value
        # that might be selected
        if "'* (any value, including the ones listed below)'" in tag_values:
            arcpy.AddMessage("\nCollecting " + tag_key + " = * (any value)")
            node_data = 'node["' + tag_key + '"]'
            way_data = 'way["' + tag_key + '"]'
            relation_data = 'relation["' + tag_key + '"]'
        # Query only for one tag value
        elif len(tag_values) == 1:
            tag_value = tag_values[0]
            arcpy.AddMessage("\nCollecting " + tag_key + " = " + tag_value)
            node_data = 'node["' + tag_key + '"="' + tag_value + '"]'
            way_data = 'way["' + tag_key + '"="' + tag_value + '"]'
            relation_data = 'relation["' + tag_key + '"="' + tag_value + '"]'
        # Query for a combination of tag values
        elif len(tag_values) > 1:
            tag_values = "|".join(tag_values)
            arcpy.AddMessage("\nCollecting " + tag_key + " = " + tag_values)
            node_data = 'node["' + tag_key + '"~"' + tag_values + '"]'
            way_data = 'way["' + tag_key + '"~"' + tag_values + '"]'
            relation_data = 'relation["' + tag_key + '"~"' + tag_values + '"]'

        query = (QUERY_START + query_date + bbox_head +
                 node_data + bbox_data +
                 way_data + bbox_data +
                 relation_data + bbox_data +
                 QUERY_END)

        arcpy.AddMessage("Issuing Overpass API query:")
        arcpy.AddMessage(query)
        response = requests.get(QUERY_URL, params={'data': query})
        if response.status_code != 200:
            arcpy.AddMessage("\tOverpass server response was %s" %
                             response.status_code)
            return
        try:
            data = response.json()
        except:
            arcpy.AddMessage("\tOverpass API responded with non JSON data: ")
            arcpy.AddError(response.text)
            return
        if len(data["elements"]) == 0:
            arcpy.AddMessage("\tNo data found!")
            return
        else:
            arcpy.AddMessage("\tCollected %s objects (including reverse "
                             "objects)" % len(data["elements"]))

        result_fcs = Toolbox.fill_feature_classes(data, parameters[7].value)
        if result_fcs[0]:
            parameters[8].value = result_fcs[0]
        if result_fcs[1]:
            parameters[9].value = result_fcs[1]
        if result_fcs[2]:
            parameters[10].value = result_fcs[2]
        return


class GetOSMDataExpert(object):
    def __init__(self):
        """Define the tool (tool name is the name of the class)."""
        self.label = "Get OSM Data (Expert Tool)"
        self.description = "Get OpenStreetMap data using fully customizable " \
                           "queries to the Overpass API"
        self.canRunInBackground = False

    def getParameterInfo(self):
        """Define parameter definitions"""
        param0 = arcpy.Parameter(
                displayName="Overpass Query",
                name="in_query",
                datatype="GPString",
                parameterType="Required",
                direction="Input"
        )
        param0.value = 'node(47.158,102.766,47.224,102.923);'
        param1 = arcpy.Parameter(
                displayName="Reference date/time UTC",
                name="in_date",
                datatype="GPDate",
                parameterType="Optional",
                direction="Input",
        )
        now = datetime.datetime.utcnow()
        param1.value = now.strftime("%d.%m.%Y %H:%M:%S")
        param_out0 = arcpy.Parameter(
                displayName="Layer containing OSM point data",
                name="out_nodes",
                datatype="GPFeatureLayer",
                parameterType="Derived",
                direction="Output"
        )
        param_out1 = arcpy.Parameter(
                displayName="Layer containing OSM line data",
                name="out_ways",
                datatype="GPFeatureLayer",
                parameterType="Derived",
                direction="Output"
        )
        param_out2 = arcpy.Parameter(
                displayName="Layer containing OSM polygon data",
                name="out_poly",
                datatype="GPFeatureLayer",
                parameterType="Derived",
                direction="Output"
        )
        return [param0, param1, param_out0, param_out1, param_out2]


    def isLicensed(self):
        """Set whether tool is licensed to execute."""
        return True


    def updateParameters(self, parameters):
        """Modify the values and properties of parameters before internal
        validation is performed.  This method is called whenever a parameter
        has been changed."""
        return


    def updateMessages(self, parameters):
        """Modify the messages created by internal validation for each tool
        parameter.  This method is called after internal validation."""
        return


    def execute(self, parameters, messages):
        """The source code of the tool."""
        # Get data using urllib

        query_date = QUERY_DATE.replace("timestamp",
                                        parameters[1].value.strftime("%Y-%m-%d"
                                                                     "T%H:%M:"
                                                                     "%SZ"))
        query = (QUERY_START + query_date + parameters[0].valueAsText +
                 QUERY_END)
        arcpy.AddMessage("Issuing Overpass API query:")
        arcpy.AddMessage(query)
        response = requests.get(QUERY_URL, params={'data': query})
        if response.status_code != 200:
            arcpy.AddMessage("\tOverpass server response was %s" %
                             response.status_code)
            return
        try:
            data = response.json()
        except:
            arcpy.AddMessage("\tOverpass API responded with non JSON data: ")
            arcpy.AddError(response.text)
            return
        if len(data["elements"]) == 0:
            arcpy.AddMessage("\tNo data found!")
            return
        else:
            arcpy.AddMessage("\nData contains no polygon features.")
        arcpy.AddMessage("\tCollected %s objects (including reverse objects)" %
                         len(data["elements"]))

        result_fcs = Toolbox.fill_feature_classes(data, parameters[1].value)
        if result_fcs[0]:
            parameters[2].value = result_fcs[0]
        if result_fcs[1]:
            parameters[3].value = result_fcs[1]
        if result_fcs[2]:
            parameters[4].value = result_fcs[2]
        return
