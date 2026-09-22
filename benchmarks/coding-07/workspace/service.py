def handle_request(req):
    route = req.get("route")
    if route == "add":
        return {"result": req["a"] + req["b"]}
    return {"error": f"unknown route {route!r}"}
