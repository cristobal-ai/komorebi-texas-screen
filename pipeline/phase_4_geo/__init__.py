"""Phase 4: one script per geo layer. Each reads data/plants.parquet, caches its raw source under data/raw/<layer>/,
writes data/layers/<layer>.parquet (one row per eia_id) and loads Supabase table layers_<layer>.

    python -m pipeline.run --phase 4 --layer transmission [--load]
"""
