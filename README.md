# The Weird Durex Finds

A Flask scrapbook-style collectible website.

## Run
1. Install Python 3.10+.
2. Open a terminal in this folder.
3. `python -m venv .venv`
4. Windows PowerShell: `.\.venv\Scripts\Activate.ps1`
5. `pip install -r requirements.txt`
6. Set environment variables:
   - `SECRET_KEY`
   - `ADMIN_PIN`
7. `python app.py`
8. Open http://127.0.0.1:5000

The app creates `durex_finds.db` automatically.

## Important
Do not put the real admin PIN into frontend JavaScript or HTML. Set it as the server environment variable `ADMIN_PIN`.
