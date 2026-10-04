# sf-pet-care
An analysis of how friendly the neighborhoods in San Francisco are to pet owners.

## Team Members

| Name | GitHubID | Role / Focus |
| --- | --- | --- |
| Tom Sasser | [GitHub ID] | [Role / Focus] |
| Zhidan Niu (Jessie) | zniu4 | Project documentation / README |
| Maurice Onyonyi | Morioh | Scripting / Registered Business |
| Jonah Robinson | [GitHub ID] | [Role / Focus] |
| Ahmad Naggayev | [GitHub ID] | [Role / Focus] |

---

## Problem Statement

We want to see whether people in different San Francisco neighborhoods have similar access to pet-care services such as veterinary clinics, grooming shops, and pet stores.

We will combine business-location data with neighborhood population data so we can compare service availability across areas instead of only counting how many businesses there are. One dataset alone would not show whether a neighborhood has many pet-care businesses because it has more residents or because pet services are actually more available there.

This could be useful for pet owners who want to know which neighborhoods have better access to everyday pet-care services.

---

## Data Sources and Integration Goal

### Sources

| # | Source & Link | Method | What it contains | Update frequency | Access requirements |
| --- | --- | --- | --- | --- | --- |
| 1 | [Registered Business Locations - San Francisco](https://data.sfgov.org/resource/g8m3-pdis.json) | API | Registered businesses in San Francisco, including business name, address, business information, dates, and geographic location | Daily | Public; no login or API key required |
| 2 | [American Community Survey 2024 5-Year Estimates](https://api.census.gov/data/2024/acs/acs5) | API | Population and demographic information for small geographic areas such as census tracts | Annual | Requires an API key |
| 3 | [San Francisco Analysis Neighborhoods](https://data.sf.gov/api/v3/views/j2bu-swwd/query.geojson?accessType=DOWNLOAD) | File (GeoJSON) | Names and geographic boundaries of San Francisco neighborhoods | Static / infrequently updated | Public; no login required |

### Integration Goal

The business dataset tells us where different pet-care businesses are located in San Francisco, including veterinary clinics, pet groomers, and pet stores. The Census dataset provides population and demographic information, while the neighborhood file gives us the official boundaries for San Francisco neighborhoods.

Combining these sources will let us compare pet-care availability across neighborhoods while considering differences in population, instead of only comparing the total number of businesses.

We can assign each business to a neighborhood by matching its latitude and longitude to the neighborhood boundary it falls inside. Census information can then be grouped or matched to the same geographic areas so we can compare the number and types of pet-care services relative to the population in different neighborhoods.

---

## Setup Instructions (Locally)

To be completed after the FastAPI code, GCP project, and environment variables are finalized.

---

## Repository Structure

To be updated as the project files are added.
