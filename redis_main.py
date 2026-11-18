import json
import time
import redis

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

def slow_query(product_id: int) -> dict:
    time.sleep(1)                                   # pretend this hits a database
    return {"id": product_id, "title": "Python Basics", "price": 12.5}

def get_product(product_id: int) -> dict:
    key = f"product:{product_id}"
    cached = r.get(key)
    if cached is not None:
        return json.loads(cached)                   # hit: fast
    product = slow_query(product_id)                # miss: slow
    r.set(key, json.dumps(product), ex=300)         # keep for 5 minutes
    return product

start = time.perf_counter(); get_product(7); print(f"first : {time.perf_counter() - start:.3f}s")
start = time.perf_counter(); get_product(7); print(f"second: {time.perf_counter() - start:.3f}s")