-- Section A offtake status from the hand-maintained data/offtake.csv (pipeline/phase_5_score/offtake.py; columns in
-- pipeline/phase_5_score/load.py SCORE_COLUMNS, a test checks). offtake_confidence (existing) = the row's confidence.
alter table public.plant_scores add column offtake_status text
  check (offtake_status in ('merchant', 'short_contract', 'long_contract_ig', 'long_contract_non_ig',
                            'long_contract_unknown_credit', 'utility_owned', 'affiliate', 'unknown'));
alter table public.plant_scores add column offtake_type text
  check (offtake_type in ('ppa', 'hedge', 'affiliate', 'utility_owned', 'merchant', 'unknown'));
alter table public.plant_scores add column offtake_counterparty text;
alter table public.plant_scores add column offtake_counterparty_ig text check (offtake_counterparty_ig in ('yes', 'no', 'unknown'));
alter table public.plant_scores add column offtake_contract_end text;          -- as published: YYYY, YYYY-MM or a date
alter table public.plant_scores add column offtake_years_left double precision; -- at scoring time; <= 0 = ended (scored merchant)
alter table public.plant_scores add column offtake_expired boolean;
alter table public.plant_scores add column offtake_share_contracted double precision;
alter table public.plant_scores add column offtake_source_url text;
alter table public.plant_scores add column offtake_source_date text;
alter table public.plant_scores add column offtake_end_basis text check (offtake_end_basis in ('published', 'computed', 'assumed'));
alter table public.plant_scores add column offtake_flags text;                  -- '; '-joined doubts (owner: flag them, don't ask)
