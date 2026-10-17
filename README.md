# sf-pet-care
An analysis of how friendly the neighborhoods in San Francisco are to pet owners.

## Team Members

| Name | GitHub ID | Role | Features |
| --- | --- | --- | --- |
| Ahmad Naggayev | ahmadavar  | Engineer | ETL integration |
| Zhidan Niu (Jessie) | zniu4 | Engineer | Community Survey Code, documentation, README |
| Maurice Onyonyi | morioh | Engineer | Business Locations |
| Jonah Robinson | jrobinson123 | Engineer | SF Neighborhood Analysis, GCP |
| Tom Sasser | tjsasser | Manager, Scrum Master, QA, Integration | Sprint, meetings, QA, integration, documentation, PR |
---

| Technology | URL |
| --- | --- |
| Git Repo | https://github.com/Morioh/sf-pet-care |
| GCP Project | https://console.cloud.google.com/welcome/new?authuser=1&project=sf-pet-care |

---

## Problem Statement

The purpose of this project is to see if different San Francisco neighborhoods have access to pet care services.  These services include veterinary clinics, grooming shops, and pet stores.  We compare service availability across the official neighborhoods in San Francisco.  Also included are  the smaller, unofficial neighborhoods within each major zone.  We use multiple data sets, including Geo Location, business and census data.  This information is useful for pet owners who want to know which neighborhoods have better access to various pet oriented services.

---

## Data Sources and Integration Goal

### Sources

Source:  Registered Business Locations — San Francisco  
URL:  https://data.sfgov.org/resource/g8m3-pdis.json
Method:  API
Contains:  Registered businesses in San Francisco including: business name, address, business information, dates, and geographic location
Update Frequency:  Daily
Access Requirements:  Public; no login or API key required

Source:  American Community Survey 2024 5 Year Estimates
URL:  https://api.census.gov/data/2024/acs/acs5
Method:  API
Contains:  Population and demographic information for small geographic areas
Update Frequency:  Daily
Access Requirements:  Requires API key

Source:  San Francisco Analysis — Neighborhoods
URL:  https://data.sf.gov/api/v3/views/j2bu-swwd/query.geojson?accessType=DOWNLOAD
Method:  File
Contains:  Names and geographic boundaries of San Francisco neighborhoods
Update Frequency:  Daily
Access Requirements:  Public, no key or login

---

### Integration Goal

This project is concerned with pet related services, including veterinary clinics, pet grooming and pet stores. The data collected by registered_businesses.py contains details and locations of 366 businesses in San Francisco.  The data collected by acs_data.py, which is Census data, provides popuation and demographic information.  Finally, the neighborhood files give access to neighborhood boundaries.  Combining this data will allow us to answer a variety of questions including locations, radius from any particular neighborhood.  The Census data will allow us to group and compare types of pet care services relative to the population in different neighborhoods.

---

You can review the FastAPI docstrings at: http://127.0.0.1:8000/docs

---

## Setup Instructions (Locally)

TBD




1) git clone https://github.com/Morioh/sf-pet-care.git
2) fastapi run neighborhood_api.py
3) python call_neighborhood_api.py

Upon running the client, you shall see the data in the GCP bucket.

---

## Repository Structure

Repo structure is also simple.  Everything resides in the root directory of the repo except the work contract.
