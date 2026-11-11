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
- Breastfeed: `[Breastfeed] Start Side`, `[Breastfeed] End Side`, `[Breastfeed] Left Duration`, `[Breastfeed] Right Duration`, `[Breastfeed] Duration Unit` (`s`, `min`, or `ms`)
- Diaper: `[Diaper] Status` (`Wet`, `Dirty`, `Mixed`, `Both`, `Dry`)
- Growth: `[Growth] Weight`, `[Growth] Weight Unit`, `[Growth] Height`, `[Growth] Height Unit`, `[Growth] Head`, `[Growth] Head Unit`
- Medical: `[Medical] Name`, `[Medical] Dose`, `[Medical] Dose Unit`, `[Temperature] Value`, `[Temperature] Unit`
- Photo: `[Photo] Filename` (the name only; the file is not fetched)

A missing duration stays unknown. A numeric zero stays zero. An unknown status, side, or unit is kept as a warning and is not rewritten into a known value. Sleep, growth, medical, and unrecognized types are stored and shown on the archive page.

`_activityKey` is the source id used for deduplication, together with the source system name `nara`.
