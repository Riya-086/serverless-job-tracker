APP = {"company": "Acme", "role": "Software Engineer", "appliedOn": "2026-10-01"}


def create(api, **overrides):
    status, body = api("POST /applications", body={**APP, **overrides})
    assert status == 201, body
    return body


def test_rejects_missing_or_wrong_token(api):
    assert api("GET /applications", token=None)[0] == 401
    assert api("GET /applications", token="wrong")[0] == 401


def test_create_and_read_back(api):
    created = create(api, url="https://jobs.example.com/1", notes="Referral from Sam")
    assert created["status"] == "applied" and created["version"] == 1
    status, fetched = api("GET /applications/{id}", path={"id": created["id"]})
    assert status == 200 and fetched == created


def test_validation_errors_name_each_field(api):
    status, body = api(
        "POST /applications", body={"company": "", "appliedOn": "2026-13-01", "status": "ghosted", "extra": 1}
    )
    assert status == 400
    assert set(body["error"]["fields"]) == {"company", "role", "appliedOn", "status", "extra"}


def test_future_dates_and_bad_json_are_rejected(api):
    assert api("POST /applications", body={**APP, "appliedOn": "2999-01-01"})[0] == 400
    from app.handler import lambda_handler

    res = lambda_handler(
        {
            "routeKey": "POST /applications",
            "headers": {"authorization": "Bearer test-token-0123456789"},
            "body": "{not json",
        },
        None,
    )
    assert res["statusCode"] == 400


def test_list_is_newest_first_and_filters_by_status(api):
    create(api, company="Old", appliedOn="2026-01-05")
    create(api, company="New", appliedOn="2026-03-10", status="interviewing")
    create(api, company="Mid", appliedOn="2026-02-01")
    _, page = api("GET /applications")
    assert [a["company"] for a in page["items"]] == ["New", "Mid", "Old"]
    _, page = api("GET /applications", query={"status": "interviewing"})
    assert [a["company"] for a in page["items"]] == ["New"]
    assert api("GET /applications", query={"status": "nope"})[0] == 400


def test_cursor_pagination_returns_every_item_once(api):
    for day in range(1, 6):
        create(api, company=f"C{day}", appliedOn=f"2026-04-0{day}")
    seen, cursor = [], None
    while True:
        _, page = api("GET /applications", query={"limit": "2", **({"cursor": cursor} if cursor else {})})
        seen += [a["company"] for a in page["items"]]
        cursor = page["nextCursor"]
        if not cursor:
            break
    assert seen == ["C5", "C4", "C3", "C2", "C1"]
    assert api("GET /applications", query={"cursor": "%%%"})[0] == 400


def test_update_uses_optimistic_concurrency(api):
    app = create(api)
    status, updated = api(
        "PATCH /applications/{id}", path={"id": app["id"]}, body={"status": "interviewing", "version": 1}
    )
    assert status == 200 and updated["version"] == 2 and updated["status"] == "interviewing"
    status, body = api("PATCH /applications/{id}", path={"id": app["id"]}, body={"status": "offer", "version": 1})
    assert status == 409 and body["error"]["currentVersion"] == 2
    assert api("PATCH /applications/{id}", path={"id": "missing"}, body={"status": "offer", "version": 1})[0] == 404
    assert api("PATCH /applications/{id}", path={"id": app["id"]}, body={"version": 2})[0] == 400


def test_delete(api):
    app = create(api)
    assert api("DELETE /applications/{id}", path={"id": app["id"]})[0] == 204
    assert api("GET /applications/{id}", path={"id": app["id"]})[0] == 404
    assert api("DELETE /applications/{id}", path={"id": app["id"]})[0] == 404


def test_stats_counts_by_status(api):
    create(api)
    create(api, status="offer")
    create(api, status="offer")
    assert api("GET /stats") == (200, {"total": 3, "byStatus": {"applied": 1, "offer": 2}})


def test_attachment_upload_url(api):
    app = create(api)
    status, body = api(
        "POST /applications/{id}/attachments", path={"id": app["id"]}, body={"filename": "My Resume (v2).pdf"}
    )
    assert status == 201
    assert body["key"].startswith(f"applications/{app['id']}/") and body["key"].endswith("My-Resume-v2-.pdf")
    assert "attachments-test" in body["uploadUrl"] and "X-Amz-Signature" in body["uploadUrl"]
    _, fetched = api("GET /applications/{id}", path={"id": app["id"]})
    assert fetched["attachments"] == [body["key"]]
    assert api("POST /applications/{id}/attachments", path={"id": app["id"]}, body={"filename": "x.exe"})[0] == 400
    assert api("POST /applications/{id}/attachments", path={"id": "missing"}, body={"filename": "a.pdf"})[0] == 404


def test_unknown_route(api):
    assert api("GET /nope")[0] == 404
