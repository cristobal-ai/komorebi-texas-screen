-- Phase 6 dossier: appraisal-district property ids of each plant's host parcels (largest first, ';'-joined).
alter table public.layers_parcels add column host_parcel_ids text;
