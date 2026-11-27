# FastAPI gemapi.dev

Construire depuis la racine avec `python -m pip install . ./webapp`.
Les ressources web sont incluses dans la wheel. Le point d’entrée est
`uvicorn digues_webapp.app:app`.

Les handlers métier et leurs dépendances historiques restent dans les sources
pour l’adaptation à venir. L’application démarre avec le shell, le catalogue
des fonds et une frontière de refus pour les entrées protégées.
