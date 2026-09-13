import httpx

auth = ("admin", "admin")
base = "http://127.0.0.1:8000"
feeds = httpx.get(f"{base}/api/feeds", auth=auth).json()["feeds"]
print("feeds", len(feeds), "enabled", sum(1 for item in feeds if item["enabled"]))

created = httpx.post(
    f"{base}/api/feeds",
    json={
        "name": "HN Frontpage",
        "url": "https://news.ycombinator.com/rss",
        "category": "technology",
        "enabled": False,
    },
    auth=auth,
)
print("custom", created.status_code, created.text[:160])

catalog = httpx.post(f"{base}/api/feeds/recommended/ars-technica", auth=auth)
print("catalog", catalog.status_code, catalog.json().get("name"), catalog.json().get("enabled"))

for item in httpx.get(f"{base}/api/feeds", auth=auth).json()["feeds"]:
    want = item["name"] == "BBC World"
    if item["enabled"] != want:
        httpx.patch(f"{base}/api/feeds/{item['id']}", json={"enabled": want}, auth=auth)

print("ingest start")
result = httpx.post(f"{base}/api/ingest", auth=auth, timeout=180)
print(result.status_code, result.json())
news = httpx.get(f"{base}/api/x3/news").json()
print("stories", len(news["stories"]), news["stories"][0]["title"] if news["stories"] else None)
tasks = httpx.get(f"{base}/api/v1/device/tasks").json()
print("pending", tasks["total_pending"])
if tasks["tasks"]:
    print("save_path", tasks["tasks"][0]["save_path"])
    print("file_url", tasks["tasks"][0]["file_url"])
