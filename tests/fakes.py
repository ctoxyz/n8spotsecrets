"""Offline transport for the tests.

Each response below has the shape the real service returned during the live runs of
2026-10-30 and 2026-11-13 (public buildings only, no request ids or IPs). Numbers are illustrative;
the tests check how the report is assembled, not what the city publishes this week.
"""
import re
import time

LA_SPRING = {
    "matchedAddress": "548 S SPRING ST, LOS ANGELES, CA, 90013",
    "coordinates": {"x": -118.2503, "y": 34.0471},
    "geographies": {
        "Census Tracts": [{"GEOID": "06037207307", "TRACT": "207307", "COUNTY": "037"}],
        "Incorporated Places": [{"NAME": "Los Angeles city"}],
        "Counties": [{"NAME": "Los Angeles County"}],
    },
}
SANTA_MONICA = {
    "matchedAddress": "1234 4TH ST, SANTA MONICA, CA, 90401",
    "coordinates": {"x": -118.4962, "y": 34.0186},
    "geographies": {
        "Census Tracts": [{"GEOID": "06037701902", "TRACT": "701902", "COUNTY": "037"}],
        "Incorporated Places": [{"NAME": "Santa Monica city"}],
        "Counties": [{"NAME": "Los Angeles County"}],
    },
}
ACS_HDR = ["NAME", "B25064_001E", "B25031_002E", "B25031_003E", "B25031_004E", "B25031_005E", "B19013_001E", "B01003_001E"]

# ---- shapes recorded 2026-11-13 around 548 S Spring St (transit, businesses, internet, 311, air, people)
TRANSIT = [{'stop_name': 'Pershing Square Station',
  'esri_agency_name': 'Metro - Los Angeles',
  'esri_stop_type_desc': 'Subway',
  'esri_route_short_names': '',
  'esri_route_count': 2,
  'esri_trip_count': 2457,
  'esri_headway_mean': 3.13953488372093,
  'esri_weekend_service': 1,
  'esri_after_2100': 1,
  'stop_lat': 34.049316,
  'stop_lon': -118.251259},
 {'stop_name': 'Pershing Square Station',
  'esri_agency_name': 'Metro - Los Angeles',
  'esri_stop_type_desc': 'Subway',
  'esri_route_short_names': '',
  'esri_route_count': 0,
  'esri_trip_count': 2457,
  'esri_headway_mean': None,
  'esri_weekend_service': 0,
  'esri_after_2100': None,
  'stop_lat': 34.049316,
  'stop_lon': -118.251259},
 {'stop_name': 'Spring / 6th',
  'esri_agency_name': 'Metro - Los Angeles',
  'esri_stop_type_desc': 'Bus',
  'esri_route_short_names': '28, 30, 33, 92',
  'esri_route_count': 4,
  'esri_trip_count': 2472,
  'esri_headway_mean': 2.8366141732283463,
  'esri_weekend_service': 1,
  'esri_after_2100': 1,
  'stop_lat': 34.046112,
  'stop_lon': -118.250908},
 {'stop_name': '6th / Broadway',
  'esri_agency_name': 'Metro - Los Angeles',
  'esri_stop_type_desc': 'Bus',
  'esri_route_short_names': '20, 720',
  'esri_route_count': 2,
  'esri_trip_count': 1751,
  'esri_headway_mean': 3.63659793814433,
  'esri_weekend_service': 1,
  'esri_after_2100': 1,
  'stop_lat': 34.046426,
  'stop_lon': -118.25185},
 {'stop_name': '6th / Spring',
  'esri_agency_name': 'Metro - Los Angeles',
  'esri_stop_type_desc': 'Bus',
  'esri_route_short_names': '16, 18, 53, 60',
  'esri_route_count': 4,
  'esri_trip_count': 3346,
  'esri_headway_mean': 2.0183098591549298,
  'esri_weekend_service': 1,
  'esri_after_2100': 1,
  'stop_lat': 34.045711,
  'stop_lon': -118.250749},
 {'stop_name': '6th / Broadway',
  'esri_agency_name': 'Metro - Los Angeles',
  'esri_stop_type_desc': 'Bus',
  'esri_route_short_names': '16, 18, 53, 55, 60, 62, 460',
  'esri_route_count': 7,
  'esri_trip_count': 4183,
  'esri_headway_mean': 1.7678132678132679,
  'esri_weekend_service': 1,
  'esri_after_2100': 1,
  'stop_lat': 34.046694,
  'stop_lon': -118.25227},
 {'stop_name': '5th / Broadway',
  'esri_agency_name': 'Metro - Los Angeles',
  'esri_stop_type_desc': 'Bus',
  'esri_route_short_names': '20, 720',
  'esri_route_count': 2,
  'esri_trip_count': 1731,
  'esri_headway_mean': 3.6743002544529264,
  'esri_weekend_service': 1,
  'esri_after_2100': 1,
  'stop_lat': 34.048354,
  'stop_lon': -118.250875}]
BIKEWAYS = [{'ST_NAME': 'MAIN ST', 'ST_FROM': '1ST ST', 'ST_TO': '2ND ST', 'Bikeway': 'Protected Bike Lane', 'Class': 4, 'Retire': 'X', 'ClassIV_Protection': 2},
 {'ST_NAME': '3RD ST',
  'ST_FROM': 'OMAR AV',
  'ST_TO': 'SAN PEDRO ST',
  'Bikeway': 'Protected Bike Lane',
  'Class': 4,
  'Retire': 'X',
  'ClassIV_Protection': 0},
 {'ST_NAME': 'GRAND AV',
  'ST_FROM': '7TH ST',
  'ST_TO': '8TH ST',
  'Bikeway': 'Protected Bike Lane',
  'Class': 4,
  'Retire': 'X',
  'ClassIV_Protection': 1},
 {'ST_NAME': '11TH ST', 'ST_FROM': 'MAPLE AV', 'ST_TO': 'SANTEE ST', 'Bikeway': 'Lane', 'Class': 2, 'Retire': ' ', 'ClassIV_Protection': 0},
 {'ST_NAME': '11TH ST', 'ST_FROM': 'MAIN ST', 'ST_TO': 'BROADWAY', 'Bikeway': 'Lane', 'Class': 2, 'Retire': ' ', 'ClassIV_Protection': 0}]
OOKLA_FIXED = {'OBJECTID': 222723,
 'QuadKey': '0230123111323003',
 'CalYear': 2026,
 'CalQuarter': 1,
 'AvgDown': 359.755,
 'AvgUp': 83.267,
 'AvgLat': 13,
 'Tests': 240,
 'Devices': 71,
 'Shape__Area': 373932,
 'Shape__Length': 2446}
OOKLA_MOBILE = {'OBJECTID': 190830,
 'QuadKey': '0230123111323003',
 'CalYear': 2026,
 'CalQuarter': 1,
 'AvgDown': 287.756,
 'AvgUp': 31.698,
 'AvgLat': 30,
 'Tests': 12,
 'Devices': 9,
 'Shape__Area': 373932,
 'Shape__Length': 2446}
BB_FIXED = [{'DBA': 'Charter Communications Inc', 'WEBSITE': 'https://business.spectrum.com/'},
 {'DBA': 'Consolidated Smart Broadband Systems, LLC', 'WEBSITE': 'https://www.consolidatedsmart.com/'},
 {'DBA': 'Sonic.net', 'WEBSITE': 'https://www.sonic.com/'},
 {'DBA': 'Ultimate Internet Access, Inc.', 'WEBSITE': 'https://www.uia.net/'},
 {'DBA': 'Vast Networks', 'WEBSITE': 'https://vastnetworks.com/'}]
BB_MOBILE = [{'DBA': 'AT&T Mobility', 'MinDnTier': 4},
 {'DBA': 'AT&T Mobility', 'MinDnTier': 6},
 {'DBA': 'Sprint', 'MinDnTier': 3},
 {'DBA': 'Sprint', 'MinDnTier': 5},
 {'DBA': 'T-Mobile', 'MinDnTier': 0},
 {'DBA': 'T-Mobile', 'MinDnTier': 2},
 {'DBA': 'T-Mobile', 'MinDnTier': 6},
 {'DBA': 'VerizonWireless', 'MinDnTier': 0},
 {'DBA': 'VerizonWireless', 'MinDnTier': 3},
 {'DBA': 'VerizonWireless', 'MinDnTier': 5}]
S311 = [{'requesttype': 'Graffiti Removal', 'n': '384'},
 {'requesttype': 'Single Streetlight Issue', 'n': '90'},
 {'requesttype': 'Bulky Items', 'n': '39'},
 {'requesttype': 'Illegal Dumping Pickup', 'n': '25'},
 {'requesttype': 'Homeless Encampment', 'n': '12'},
 {'requesttype': 'Metal/Household Appliances', 'n': '8'},
 {'requesttype': 'Dead Animal Removal', 'n': '7'},
 {'requesttype': 'Multiple Streetlight Issue', 'n': '6'},
 {'requesttype': 'Electronic Waste', 'n': '4'},
 {'requesttype': 'Other', 'n': '3'}]
CES = {'tract': 6037207301,
 'pm': 12.52185449,
 'pmP': 91.1636589919104,
 'diesel': 1.796607322,
 'dieselP': 99.6017423771002,
 'traffic': 669.7749357,
 'trafficP': 33.675,
 'CIscoreP': 90.052950075643}
HOMELESS = {'CSA_Label': 'Los Angeles - Downtown',
 'Total_Unsheltered_Pop': 868,
 'Total_Sheltered_Pop': 361,
 'Total_Pop': 1229,
 'Density_Unsheltered': 364.0248560881762}
COUNCIL = {'NAME': 'DOWNTOWN LOS ANGELES',
 'NC_Name': 'Downtown Los Angeles',
 'DWEBSITE': 'http://empowerla.org/DLANC',
 'SERVICE_RE': 'REGION 6 - CENTRAL 2',
 'NC_ID': 52}
# The city housing department's look-up datasets. Rows marked (probe) are as the live sources returned
# them on 14 Nov 2026; the others are made in the same shape to cover open cases, several kinds and amounts.
LAHD_ENFORCE = [
    {"apn": "4263009030", "officialaddress": "11700 W WILSHIRE BLVD, Los Angeles, CA 90025", "case_filed_date": "2015-10-29T00:00:00.000",
     "closed_date": "2016-01-06T00:00:00.000", "casetype": "Reduction of Services"},                                        # (probe)
    {"apn": "4263009030", "officialaddress": "11700 W WILSHIRE BLVD, Los Angeles, CA 90025", "case_filed_date": "2018-11-05T00:00:00.000",
     "closed_date": "2018-12-06T00:00:00.000", "casetype": "Unit Not Registered, Illegal Eviction, Reduction of Services"},   # casetype shape (probe)
    {"apn": "4263009030", "officialaddress": "11700 W WILSHIRE BLVD, Los Angeles, CA 90025", "case_filed_date": "2026-03-02T00:00:00.000",
     "casetype": "Illegal Eviction"},
]
LAHD_CCRIS = [
    {"apn": "4263009030", "start_date": "2021-01-15T00:00:00.000", "end_date": "2021-03-18T00:00:00.000", "totalcomplaintscount": "1",
     "opencomplaintscount": "0", "scheduledinspectionscount": "0", "casetype": "URP", "address": "11728    WILSHIRE BLVD   LOS ANGELES, CA 90025"},  # (probe)
    {"apn": "4263009030", "start_date": "2026-11-02T00:00:00.000", "end_date": "2026-11-02T00:00:00.000", "totalcomplaintscount": "3",
     "opencomplaintscount": "2", "scheduledinspectionscount": "0", "casetype": "Complaint", "address": "11728    WILSHIRE BLVD   LOS ANGELES, CA 90025"},
    {"apn": "4263009030", "start_date": "2024-06-01T00:00:00.000", "totalcomplaintscount": "1",
     "opencomplaintscount": "0", "scheduledinspectionscount": "1", "casetype": "Complaint", "address": "11728    WILSHIRE BLVD   LOS ANGELES, CA 90025"},
]
LAHD_BUYOUT = [
    {"apn": "4263009030", "disclosure_fileddate": "2024-04-16T00:00:00.000", "tenant_streetaddress": "11728 Wilshire Blvd", "compensation_amount": "$24,050.00"},  # (probe)
    {"apn": "4263009030", "disclosure_fileddate": "2017-02-09T00:00:00.000", "tenant_streetaddress": "11728 Wilshire Blvd", "compensation_amount": "$20,416.00"},  # amount (probe)
    {"apn": "4263009030", "disclosure_fileddate": "2025-01-20T00:00:00.000", "tenant_streetaddress": "11728 Wilshire Blvd", "compensation_amount": "$31,000.00"},
]
LAHD_DECL = [
    {"apn": "4263009030", "officialaddress": "11700 W WILSHIRE BLVD, Los Angeles, CA 90025", "case_filed_date": "2023-05-08T00:00:00.000",
     "closed_date": "2023-12-27T00:00:00.000", "casetype": "Notice of Intent to Withdraw"},                                  # (probe)
    {"apn": "4263009030", "officialaddress": "11700 W WILSHIRE BLVD, Los Angeles, CA 90025", "case_filed_date": "2023-06-05T00:00:00.000",
     "closed_date": "2023-12-12T00:00:00.000", "casetype": "Government Agency\u2019s Order"},                                # casetype (probe)
]
LAHD_AFFORDABLE = [{"apn": "2654011020", "project_number": "19-126521", "name": "THE MAIN", "development_stage": "Development",
                    "construction_type": "NEW CONSTRUCTION", "address": "15302 W RAYEN ST  North Hills, CA 91343", "site_units": "64",
                    "project_total_units": "64", "housing_type": "SPECIAL NEEDS", "supportive_housing": "Yes"}]              # (probe)

HUD_FMR_ROW = {"FMR_AREANAME": "Los Angeles-Long Beach-Glendale, CA HUD Metro FMR Area", "FMR_0BDR": 1863, "FMR_1BDR": 2085,
               "FMR_2BDR": 2601, "FMR_3BDR": 3298, "FMR_4BDR": 3672}                                # (probe)
HUD_HCV = {"06037207307": (107, 11.84000015258789), "06037267404": (131, 7.519999980926514)}      # as live, 14 Nov 2026

EVICT = [{'apn': '5534026024', 'eviction_category': 'At-Fault', 'notice_date': '2026-10-24T00:00:00.000', 'notice_type': '3 Day'},
 {'apn': '5146033042', 'eviction_category': 'At-Fault', 'notice_date': '2026-10-22T00:00:00.000', 'notice_type': '3 Day'},
 {'apn': '5146033042', 'eviction_category': 'At-Fault', 'notice_date': '2026-10-22T00:00:00.000', 'notice_type': '3 Day'}]
CRIME_BY_YEAR = [{'yr': '2020-01-01T00:00:00.000', 'n': '1263'},
 {'yr': '2021-01-01T00:00:00.000', 'n': '1295'},
 {'yr': '2022-01-01T00:00:00.000', 'n': '2237'},
 {'yr': '2023-01-01T00:00:00.000', 'n': '1986'},
 {'yr': '2024-01-01T00:00:00.000', 'n': '906'}]
CRIME_DISTRICT = [{'rpt_dist_no': '0153', 'area_name': 'Central', 'n': '3005'}]
BIZ_COUNTS = [{'primary_naics_description': 'Jewelry stores', 'n': '426'},
 {'primary_naics_description': 'Legal services', 'n': '417'},
 {'primary_naics_description': 'Full-service restaurants', 'n': '162'},
 {'primary_naics_description': 'Limited-service eating places', 'n': '106'},
 {'primary_naics_description': 'Traveler accommodation (including hotels, motels, & bed & breakfast inns)', 'n': '44'},
 {'primary_naics_description': 'Grocery stores (including supermarkets & convenience stores without gas)', 'n': '31'}]
ACS_PEOPLE = [['NAME',
  'B25003_001E',
  'B25003_003E',
  'B01002_001E',
  'B08301_001E',
  'B08301_010E',
  'B08301_019E',
  'B08301_021E',
  'B08013_001E',
  'B28002_001E',
  'B28002_004E',
  'B28002_013E',
  'state',
  'county'],
 ['Los Angeles County, California',
  '3390254',
  '1828047',
  '37.9',
  '4705602',
  '199924',
  '114249',
  '714187',
  '122903390',
  '3390254',
  '3108381',
  '213429',
  '06',
  '037']]
BIZ_NAMED = [
    {"business_name": "EXAMPLE MARKET INC", "dba_name": "SPRING ST MARKET", "street_address": "600 S SPRING STREET",
     "primary_naics_description": "Grocery stores (including supermarkets & convenience stores without gas)",
     "location_1": {"latitude": "34.0462", "longitude": "-118.2512", "human_address": "{}"}},
    {"business_name": "EXAMPLE FOODS LLC", "dba_name": "", "street_address": "701 S HILL STREET",
     "primary_naics_description": "Grocery stores (including supermarkets & convenience stores without gas)",
     "location_1": {"latitude": "34.0452", "longitude": "-118.2551", "human_address": "{}"}},
    {"business_name": "EXAMPLE RX CORP", "dba_name": "BROADWAY PHARMACY", "street_address": "500 S BROADWAY",
     "primary_naics_description": "Pharmacies & drug stores",
     "location_1": {"latitude": "34.0478", "longitude": "-118.2507", "human_address": "{}"}},
]
PARKS = [{"Name": "PERSHING SQUARE", "Park_Class": "N", "Area_Acres": 4.44}]
NIBRS_MONTHS = ([{"m": "2025-%02d-01T00:00:00.000" % i, "n": str(n)} for i, n in enumerate((60, 52, 58, 55, 61, 50, 57, 54, 56, 59, 53, 53), 1)]
                + [{"m": "2026-%02d-01T00:00:00.000" % i, "n": str(n)} for i, n in enumerate((45, 40, 44, 41, 47, 39, 43, 42, 40, 36), 1)])


# ---- parcels. Rows marked REAL are the county's own answers of 2026-11-13 (n8spot-parcel-probe2.txt),
# cut to the fields the code asks for. UseCode / UseType on the 548 unit rows and their centroid were
# not printed by that probe and are assumed here; the deploy gate checks them against the live service.
CENSUS_453 = {   # REAL: note the point is about 70 m from the parcel centroid below
    "matchedAddress": "453 S SPRING ST, LOS ANGELES, CA, 90013",
    "coordinates": {"x": -118.249052471479, "y": 34.048099009122},
    "geographies": LA_SPRING["geographies"],
}
CENSUS_WILSHIRE = {   # made up: shape only
    "matchedAddress": "11740 WILSHIRE BLVD, LOS ANGELES, CA, 90025",
    "coordinates": {"x": -118.4610, "y": 34.0500},
    "geographies": LA_SPRING["geographies"],
}
ARCGIS_WILSHIRE = {   # made up: shape per the ArcGIS World geocoder documentation
    "address": "11740 Wilshire Blvd, Los Angeles, California, 90025", "location": {"x": -118.4615, "y": 34.0506}, "score": 100,
    "attributes": {"Addr_type": "PointAddress", "Match_addr": "11740 Wilshire Blvd, Los Angeles, California, 90025", "AddNum": "11740",
                   "StAddr": "11740 Wilshire Blvd", "City": "Los Angeles", "Subregion": "Los Angeles County", "Postal": "90025"},
}
ARCGIS_453 = {   # REAL point and match text (deploy gate of 2026-11-13); no City field on purpose, so the city is read from the match text
    "address": "453 S Spring St, Suite 400, Los Angeles, California, 90013", "location": {"x": -118.249706597184, "y": 34.047676308103}, "score": 100,
    "attributes": {"Addr_type": "Subaddress", "Match_addr": "453 S Spring St, Suite 400, Los Angeles, California, 90013", "AddNum": "453",
                   "StAddr": "453 S Spring St", "Subregion": "Los Angeles County", "Postal": "90013"},
}


def _unit(ain, unit, sqft):
    return {"AIN": ain, "SitusHouseNo": "548", "SitusFraction": " ", "SitusDirection": "S", "SitusUnit": unit, "SitusStreet": "SPRING ST.",
            "SitusCity": "LOS ANGELES, CA", "SitusZIP": "90013", "UseCode": "010C", "UseType": "Residential", "UseDescription": "Single",
            "YearBuilt1": "1914", "EffectiveYear1": "1914", "Units1": 1, "Bedrooms1": 0, "Bathrooms1": 1, "SQFTmain1": sqft,
            "CENTER_LAT": 34.04595165, "CENTER_LON": -118.25047182}


PARCELS = [
    # REAL 453 S Spring: one office parcel
    {"AIN": "5149024001", "SitusHouseNo": "453", "SitusFraction": " ", "SitusDirection": "S", "SitusUnit": " ", "SitusStreet": "SPRING ST",
     "SitusCity": "LOS ANGELES CA", "SitusZIP": "90013-2001", "UseCode": "1706", "UseType": "Commercial", "UseDescription": "Office Buildings",
     "YearBuilt1": "1914", "EffectiveYear1": "1928", "Units1": 0, "Bedrooms1": 0, "Bathrooms1": 0, "SQFTmain1": 193816,
     "CENTER_LAT": 34.04778662, "CENTER_LON": -118.24974753},
    # REAL 548 S Spring: a store unit with no year, then one parcel per condo unit (first 11 of them)
    {"AIN": "5149036019", "SitusHouseNo": "548", "SitusFraction": " ", "SitusDirection": "S", "SitusUnit": "STE 106", "SitusStreet": "SPRING ST",
     "SitusCity": "LOS ANGELES CA", "SitusZIP": "90013-2345", "UseCode": "1216", "UseType": "Commercial", "UseDescription": "Store Combination",
     "YearBuilt1": "0000", "EffectiveYear1": "0000", "Units1": 1, "Bedrooms1": 0, "Bathrooms1": 0, "SQFTmain1": 300,
     "CENTER_LAT": 34.04595165, "CENTER_LON": -118.25047182},
    _unit("5149036020", "B1", 1250), _unit("5149036021", "B2", 1520), _unit("5149036022", "B3", 950), _unit("5149036023", "B4", 1000),
    _unit("5149036024", "B5", 1240), _unit("5149036025", "B6", 1540), _unit("5149036026", "B7", 1960), _unit("5149036027", "B8", 630),
    _unit("5149036028", "B9", 830), _unit("5149036029", "B10", 880), _unit("5149036030", "B11", 1510),
    # made up, to prove what must be rejected: same number on North Spring, and on a street that only starts with SPRING
    {"AIN": "5408001001", "SitusHouseNo": "548", "SitusDirection": "N", "SitusUnit": " ", "SitusStreet": "SPRING ST", "UseCode": "3100",
     "UseType": "Industrial", "UseDescription": "Light Manufacturing", "YearBuilt1": "1950", "Units1": 0, "SQFTmain1": 9000,
     "CENTER_LAT": 34.0452, "CENTER_LON": -118.2490},
    {"AIN": "7100001001", "SitusHouseNo": "548", "SitusDirection": " ", "SitusUnit": " ", "SitusStreet": "SPRINGDALE DR", "UseCode": "0100",
     "UseType": "Residential", "UseDescription": "Single", "YearBuilt1": "1960", "Units1": 1, "SQFTmain1": 1400,
     "CENTER_LAT": 34.0460, "CENTER_LON": -118.2504},
    {"AIN": "5149035012", "SitusHouseNo": "525", "SitusDirection": "S", "SitusUnit": " ", "SitusStreet": "SPRING ST", "UseCode": "2700",
     "UseType": "Commercial", "UseDescription": "Parking Lots (Commercial Use Properties)", "YearBuilt1": "0000", "Units1": 0, "SQFTmain1": 0,
     "CENTER_LAT": 34.0468, "CENTER_LON": -118.2501},
    # made up: a complex whose assessor address (11728) is not the address people use (11740)
    {"AIN": "4324001010", "SitusHouseNo": "11728", "SitusDirection": " ", "SitusUnit": " ", "SitusStreet": "WILSHIRE BLVD", "UseCode": "0500",
     "UseType": "Residential", "UseDescription": "Five or more apartments", "YearBuilt1": "1961", "EffectiveYear1": "1961", "Units1": 712,
     "Bedrooms1": 0, "Bathrooms1": 0, "SQFTmain1": 800000, "SitusCity": "LOS ANGELES CA", "SitusZIP": "90025",
     "CENTER_LAT": 34.0506, "CENTER_LON": -118.4615},
]


class Transport:
    """Callable used as n8spotsecrets.http.TRANSPORT. `slow` maps a url fragment to seconds of delay."""

    def __init__(self, slow=None, fail=None, suppress_tract_rent=False, variant=()):
        self.slow = slow or {}
        self.fail = fail or set()
        self.variant = set(variant)
        self.suppress_tract_rent = suppress_tract_rent
        self.calls = []
        self.asked = []

    def __call__(self, url, p, data):
        self.calls.append(url)
        self.asked.append((url, dict(p or {})))
        for frag, secs in self.slow.items():
            if frag in url:
                time.sleep(secs)
        for frag in self.fail:
            if frag in url:
                return RuntimeError("HTTP 503 from " + frag)
        if "onelineaddress" in url:
            a = p.get("address", "")
            if a.startswith("453 "):
                return {"result": {"addressMatches": [CENSUS_453]}}
            if a.startswith("530 "):   # made up: a number the assessor does not list, with neighbors on both sides
                return {"result": {"addressMatches": [dict(LA_SPRING, matchedAddress="530 S SPRING ST, LOS ANGELES, CA, 90013")]}}
            if a.startswith("11740 "):
                return {"result": {"addressMatches": [CENSUS_WILSHIRE]}}
            if "Santa Monica" in a:
                return {"result": {"addressMatches": [SANTA_MONICA]}}
            if not any(ch.isdigit() for ch in a):
                return {"result": {"addressMatches": []}}
            return {"result": {"addressMatches": [LA_SPRING]}}
        if "LACounty_Parcel" in url:
            if p.get("distance"):
                return RuntimeError("the buffered parcel search must not be used: the county caps rows before its distance test")
            m = re.search(r"SitusHouseNo='(\d+)' AND SitusStreet LIKE '([^%]+)%'", p.get("where", ""))
            if m:
                rows = [a for a in PARCELS if a["SitusHouseNo"] == m.group(1) and a["SitusStreet"].startswith(m.group(2))]
            else:
                g = [float(v) for v in p.get("geometry", "0,0").split(",")]
                if len(g) == 4:      # envelope
                    rows = [a for a in PARCELS if g[0] <= a["CENTER_LON"] <= g[2] and g[1] <= a["CENTER_LAT"] <= g[3]]
                else:                # point: stands in for "the polygon contains the point"
                    rows = [a for a in PARCELS if abs(a["CENTER_LAT"] - g[1]) < 0.0002 and abs(a["CENTER_LON"] - g[0]) < 0.0002]
            return {"features": [{"attributes": a} for a in rows]}
        if "findAddressCandidates" in url:
            if p.get("singleLine", "").startswith("11740"):
                return {"candidates": [ARCGIS_WILSHIRE]}
            if p.get("singleLine", "").startswith("453 "):
                return {"candidates": [ARCGIS_453]}
            return {"candidates": []}
        if "geographies/coordinates" in url:
            return {"result": {"geographies": LA_SPRING["geographies"]}}
        if "tigerWMS_Current/MapServer/identify" in url:
            return {"results": [{"layerName": "Census Tracts", "attributes": {"GEOID": "06037265301", "TRACT": "265301", "COUNTY": "037"}},
                                {"layerName": "Incorporated Places", "attributes": {"NAME": "Los Angeles city"}}]}
        if "NFHL" in url:
            return {"features": [{"attributes": {"FLD_ZONE": "X", "ZONE_SUBTY": "1 PCT DEPTH LESS THAN 1 FOOT", "SFHA_TF": "F"}}]}
        if "Fault_Traces" in url or "Fire_Department" in url:
            return {"count": 0}
        if "FHSZ" in url:
            return RuntimeError("HTTP 400")
        if url.endswith("Transportation/MapServer"):
            return {"layers": [{"id": 1, "name": "Primary Roads", "subLayerIds": [2]}, {"id": 2, "name": "Primary Roads"},
                               {"id": 3, "name": "Secondary Roads"}, {"id": 9, "name": "Railroads"}]}
        if "Transportation/MapServer/" in url:
            if url.endswith("/3/query"):
                return {"features": [{"attributes": {"FULLNAME": "S Spring St"}}, {"attributes": {"FULLNAME": "W 5th St"}}]}
            return {"features": []}
        if "/resource/2nrs-mtv8" in url:
            sel = p.get("$select", "")
            if "max(" in sel:
                return [{"newest": "2024-12-30T00:00:00.000"}]
            if "date_trunc_y" in sel:
                return CRIME_BY_YEAR
            if "rpt_dist_no" in sel:
                return CRIME_DISTRICT
            if "time_occ" in p.get("$where", ""):
                return [{"n": "611"}]
            if "$group" in p:
                return [{"crm_cd_desc": "BURGLARY FROM VEHICLE", "n": "212"}, {"crm_cd_desc": "BATTERY - SIMPLE ASSAULT", "n": "130"}]
            m = re.search(r"lat between ([-0-9.]+) and ([-0-9.]+)", p.get("$where", ""))
            near = bool(m) and float(m.group(2)) - float(m.group(1)) < 0.004      # the 150 m box
            if near and "near-box" in self.fail:
                return RuntimeError("HTTP 503 from near-box")
            if "like" in p.get("$where", ""):
                return [{"n": "201" if near else "227"}]
            return [{"n": "506" if near else "1561"}]
        if ("/resource/pi9x-tg5x" in url or "/resource/dyxf-7hc4" in url):
            if p.get("$limit") == "1":
                return [{"permit_nbr": "x", "location_1": {"type": "Point", "coordinates": [-118.25, 34.04]}}]
            assert "within_circle(location_1" in p["$where"]
            return [{"permit_nbr": "1", "primary_address": "548 S SPRING ST", "issue_date": "2025-03-27T00:00:00.000",
                     "permit_type": "Bldg-Alter/Repair", "work_desc": "INTERIOR REMODEL", "status_desc": "Permit Finaled"}]
        if "/resource/u82d-eh7z" in url:
            assert " in ('" in p["$where"]
            return []
        if "/resource/rken-a55j" in url:
            return [{"stat": "Closed", "resdttm": "2019-06-01T00:00:00", "csetyp": "Sign"}]
        if "/resource/cr8f-uc4j" in url:
            return []
        if "/resource/k7nn-b2ep" in url:
            return NIBRS_MONTHS if "date_trunc_ym" in p.get("$select", "") else [{"n": "118"}]
        if "Transit_Stops" in url:
            assert p.get("distance") == "800"
            return {"features": [{"attributes": a} for a in TRANSIT]}
        if "LA_City_Bikeways" in url:
            return {"features": [{"attributes": a} for a in BIKEWAYS]}
        if "/resource/6rrh-rzua" in url:
            assert "within_circle(location_1" in p["$where"]
            return BIZ_COUNTS if "$group" in p else BIZ_NAMED
        if "LARAP_Parks" in url:
            return {"features": [{"attributes": a} for a in PARKS]}
        if "Speedtest_by_Ookla" in url:
            return {"features": [{"attributes": OOKLA_FIXED if url.endswith("/0/query") else OOKLA_MOBILE}]}
        if "Fixed_Business_Optimized" in url:
            return {"features": [{"attributes": a} for a in BB_FIXED]}
        if "CA_Broadband_Availability_Mobile" in url:
            return {"features": [{"attributes": a} for a in BB_MOBILE]}
        if "/resource/h73f-gn57" in url:
            return S311
        if "CalEnviroScreen" in url:
            return {"features": [{"attributes": CES}]}
        if "Homeless_Counts" in url:
            return {"features": [{"attributes": HOMELESS}]}
        if "Neighborhood_Councils" in url:
            return {"features": [{"attributes": COUNCIL}]}
        if "DRP/Open_Data" in url or "CGS_Liquefaction" in url or "CGS_Landslide" in url:
            return {"features": []}
        if "/resource/dqq4-799r" in url:
            if "1831 N VINE" in p.get("$where", ""):
                return [{"address": "1831 N VINE ST", "zip": "90028", "permit": "19016-10000-24965", "issued": "2022-05-12T00:00:00.000",
                         "step_3_compl_date": "2023-08-31T00:00:00.000", "step3_tollingextdate": "2026-08-09T00:00:00.000"}]
            return []
        if "/resource/2u8b-eyuu" in url:
            return EVICT[1:]
        if "/resource/eagk-wq48" in url:
            return [] if "empty-file" in self.variant else LAHD_ENFORCE
        if "/resource/ds2y-sb5t" in url:
            return [] if "empty-file" in self.variant else LAHD_CCRIS
        if "/resource/ci3m-f23k" in url:
            return [] if "empty-file" in self.variant else LAHD_BUYOUT
        if "/resource/vpax-89xu" in url:
            return [] if "empty-file" in self.variant else LAHD_DECL
        if "Fair_Market_Rents" in url:
            if "geometry" in p and "by-point-ok" not in self.variant:
                return RuntimeError("The read operation timed out")              # as live, 14 Nov 2026: by map point is slow
            if "geometry" not in p and p.get("where") != "FMR_AREANAME = 'Los Angeles-Long Beach-Glendale, CA HUD Metro FMR Area'":
                return {"features": []}
            return {"features": [{"attributes": HUD_FMR_ROW}]}
        if "Housing_Choice_Vouchers_by_Tract" in url:
            if "geometry" in p:
                return RuntimeError("The read operation timed out")              # as live: 3 of 3 by map point timed out
            m = re.fullmatch(r"GEOID = '(\d{11})'", p.get("where", ""))
            if not m or m.group(1) not in HUD_HCV or "hcv-no-row" in self.variant:
                return {"features": []}
            n, pct = (None, None) if "hcv-null" in self.variant else HUD_HCV[m.group(1)]
            return {"features": [{"attributes": {"GEOID": m.group(1), "HCV_PUBLIC": n, "HCV_PUBLIC_PCT": pct}}]}
        if "/resource/mymu-zi3s" in url:
            if "'" in p.get("$where", "") or not re.fullmatch(r"apn = \d{10}", p.get("$where", "")):
                return RuntimeError('HTTP 400 {"message":"Query coordinator error: query.soql.type-mismatch; Type mismatch for #IN, is text"}')  # as live, 14 Nov 2026
            return LAHD_AFFORDABLE if "affordable" in self.variant else []
        if "api.census.gov" in url:
            if "B25003" in p.get("get", ""):
                return ACS_PEOPLE
            if "zip code" in p.get("for", ""):
                return [ACS_HDR, ["ZCTA5 90013", "1967", "1500", "1900", "2500", "3000", "40000", "12000"]]
            if self.suppress_tract_rent:
                return [ACS_HDR, ["Census Tract 2073.07", "-666666666", "-666666666", "-666666666", "-666666666", "-666666666", "61000", "4100"]]
            return [ACS_HDR, ["Census Tract", "2445", "1800", "2300", "3100", "4000", "90000", "4000"]]
        return RuntimeError("no fixture for " + url[:80])
