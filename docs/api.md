# API

Start with `uvicorn astra_grid.api:app --reload`. The JSON endpoints are listed in the README. Send `POST /api/simulations` first, retain `run_id`, then call `step` or bounded `run`. All run data is in memory.
