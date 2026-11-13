# Nara Baby CSV columns this importer reads

Nara's export is one CSV per profile. The header is not fixed: columns exist for the types present in that file. This importer matches columns by name.

Shared columns:

- `Type`
- `Profile Name`
- `Start Date/time` and `Start Date/time (Epoch)` (epoch milliseconds)
- `End Date/time` and `End Date/time (Epoch)`
- `Created By Caregiver` and `Last Updated By Caregiver`
- `Note`
- `Time Zone` (IANA name)
- `_familyKey`, `_profileKey`, `_activityKey`

Type-specific columns used by the synthetic fixtures and the parser:

- Profile: `[Profile] Birth Date`, `[Profile] Birth Date (Adjusted)`, `[Profile] Sex`, `[Profile] Type`
- Breastfeed: `[Breastfeed] Start Side` or `[Breastfeed] Begin Side`, `[Breastfeed] End Side`, `[Breastfeed] Left Duration`, `[Breastfeed] Right Duration`, `[Breastfeed] Duration Unit` (`s`, `min`, or `ms`). A side may end in `.nonTimer` (`LEFT.nonTimer`, `RIGHT.nonTimer`); the suffix is removed and the feed is marked manual.
- Bottle feed: `[Bottle Feed] Type` (`Breast Milk`, `Formula`, `Breast Milk Formula`, or empty), `[Bottle Feed] Volume` and `[Bottle Feed] Volume Unit` (`ML`, `FLOZ`, or empty), `[Bottle Feed] Breast Milk Volume`, `[Bottle Feed] Formula Volume` and their units, `[Bottle Feed] Formula Name`. An empty volume is unknown and does not warn. An empty unit warns only when a volume is present.
- Pump: `[Pump] Left Volume`, `[Pump] Right Volume`, `[Pump] Total Volume` and their units, `[Pump] Duration (Seconds)`, `[Pump] End Date/time (Epoch)`.
- Diaper: `[Diaper] Type` (`Wet`, `Dirty`, `Dirty Wet`, `Dry`), with `[Diaper] Status` as a fallback. `[Diaper] Detail`, `[Diaper] Dirty Color`, and `[Diaper] Dirty Texture` are stored and do not warn.
- Sleep: `[Sleep] End Date/time`, `[Sleep] Duration (Seconds)`, `[Sleep] End Date/time (Epoch)`, in addition to the shared end columns.
- Growth: `[Growth] Weight`, `[Growth] Weight Unit`, `[Growth] Height`, `[Growth] Height Unit`, `[Growth] Head Size` or `[Growth] Head`, and `[Growth] Head Size Unit` or `[Growth] Head Unit`.
- Medical: `[Medical] Medication` or `[Medical] Name`, `[Medical] Dose`, `[Medical] Dose Unit`, `[Temperature] Value`, `[Temperature] Unit`.
- Photo: `[Photo] Filename` (the name only; the file is not fetched)

A missing duration stays unknown. A numeric zero stays zero. An unknown status, side, or unit is kept as a warning and is not rewritten into a known value. Sleep, growth, medical, and unrecognized types are stored and shown on the archive page.

`_activityKey` is the source id used for deduplication, together with the source system name `nara`.
