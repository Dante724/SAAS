# Asset Identification — VIN/HIN/IMEI Lookups & Industry Templates

Orderry-style asset naming plus identifier-kind-aware forms. Verified behavior
we mirror: Orderry renames the asset entity per business type ("Vehicle,
Device, Appliance, Tool… depending on the type of business you choose") and
uses one unified "Serial Number / IMEI / VIN" field. Tekly goes further: the
identifier *kind* switches with the asset type, with live lookups.

## Identifier kinds (asset type → identifier)
Car / Truck / RV / Bike / Powersports → **VIN** · Marine → **HIN** ·
Phone → **IMEI** · Computer / Tablet / Tool / Small engine / Appliance / other → **Serial**.
Mapping is served by the templates endpoint so all forms share one source.

## Lookup API (under integrations, JWT)
| Endpoint | Behaviour |
|---|---|
| `GET /api/integrations/lookup/templates` | Template registry, the org's current template (label/singular/asset_types), asset-type→kind map, kind labels. One call powers every asset form. |
| `GET /api/integrations/lookup/vin/{vin}` | ISO-3779 structural validation (17 chars, no I/O/Q, **check digit**), WMI region + model year; **live NHTSA vPIC enrichment** (keyless, best-effort — `VIN_LOOKUP_LIVE`, falls back to structural offline) adds make/model/year/vehicle_type. |
| `GET /api/integrations/lookup/hin/{hin}` | USCG 33 CFR 181 parse: MIC, hull serial, certification month/year, model year. Manufacturer from an embedded sample map (full resolution = USCG MIC database seam). |
| `GET /api/integrations/lookup/imei/{imei}` | Luhn validation, TAC/serial split; TAC→model needs a provider key (`IMEI_LOOKUP_API_KEY`, simulated absent). |

## Industry template (Settings → General & company)
`Organisation.industry_template` ∈ generic · auto_repair · powersports · marine ·
device_repair · small_engine · tool_repair · appliance_repair. Renames the asset
entity (Assets/Vehicles/Vessels/Devices/Equipment/Tools/Appliances) and sets the
default asset-type list. `GET/PUT /api/staff/company` carries it (422 on unknown).

## Data model
`Device.vin` + `Device.hin` (alongside serial/imei); asset search matches all
four. `POST /api/clients/{id}/devices`, `POST /api/assets`, and order create
(`asset_vin`/`asset_hin`/`asset_imei`) all accept them; order detail returns the
full identifier set on `device`.

## UI behaviour
- **Work order / estimate modal**: asset-type list comes from the org template;
  the identifier label switches (VIN/HIN/IMEI/Serial) as the type changes; a
  lookup button validates and auto-fills brand/model/year.
- **Client device modal**: asset type + VIN/HIN field with the same lookup.
- **Assets page**: identifier column shows VIN→HIN→serial→IMEI; search covers all.

## Tests
`backend/tests/test_asset_identifiers.py` (5): VIN structural valid/invalid
check digit, HIN + IMEI decode + invalid Luhn, template registry + org setting
round-trip + 422, device persists HIN, order auto-creates asset with VIN.

Caveats: NHTSA enrichment needs outbound network (structural decode is the
offline floor and is what tests pin); HIN manufacturer names beyond the sample
map report the MIC; IMEI model resolution requires a commercial TAC provider.
