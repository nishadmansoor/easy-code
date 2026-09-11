# MiniApp

MiniApp is a small example web application used as a benchmark fixture for
EasyCode. It exposes a login endpoint backed by an in-memory user store.

## Architecture

Requests enter through `app/main.py`, which registers routes from
`app/auth/routes.py`. Authentication logic lives in `app/auth/service.py`, user
lookup in `app/users/repository.py`, and session issuing in
`app/core/session.py`.
