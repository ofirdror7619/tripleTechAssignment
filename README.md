# Average Temperature Exercise

## Flow

1. The app starts with an auth request:

POST https://gw4favkunc.execute-api.il-central-1.amazonaws.com/auth
Body:
{}

The auth response contains:
{
  "token": "...",
  "expires_at": "2026-05-29T14:15:54+00:00",
  "data_url": "https://gw4favkunc.execute-api.il-central-1.amazonaws.com/data",
  "dataset": "delhi",
  "request_id": "..."
}

The app stores `token`, `expires_at`, `data_url`, `dataset`, and `request_id` in an `AuthInfo` model.

2. The app fetches weather data from `data_url`.

The data endpoint also uses `POST`.

Page 1 is fetched first because it tells us how many pages exist:


POST {data_url}?request_id={request_id}&page=1
Headers:
Authorization: Bearer {token}
Content-Type: application/json
Accept: application/json

Body:
{}

After page 1 returns `total_pages`, the app fetches pages `2..total_pages` in parallel using a configurable worker limit:

"max_parallel_requests": 5


Each page request still uses the same token and request id:

POST {data_url}?request_id={request_id}&page=2
POST {data_url}?request_id={request_id}&page=3


3. Before each page request, `weather_service.py` checks whether the token is expired or close to expiring.

If the token is expired, the service raises a `401`-style `ApiError`. `main.py` catches that, re-authenticates, and restarts the data fetch with a fresh token and fresh request id.

4. The app handles temporary failures with retries.

Retries are configured in `src/config/config.json`:

{
  "max_retries": 6,
  "transient_statuses": [408, 429, 500, 502, 503, 504]
}


5. The app calculates the average temperature.

Each page has records like:

{
  "date": "2024-01-01",
  "temperature_noon_c": 19.1
}

The app:

1. Extracts every `temperature_noon_c`
2. Adds it to `total_temperature`
3. Increments `item_count`
4. Returns `total_temperature / item_count`

Final output:

{
City: venice
Average temperature: 20.175
}

The average is formatted with up to 3 decimal digits.
