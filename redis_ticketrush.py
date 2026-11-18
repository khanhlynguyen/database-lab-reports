import time
import uuid
from concurrent.futures import ThreadPoolExecutor
import redis

# Kết nối tới Redis server
r = redis.Redis(host="localhost", port=6379, decode_responses=True)

def race(fn, n=50):
    """Chạy hàm fn(i) từ n thread đồng thời và trả về danh sách kết quả."""
    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(fn, range(n)))

# --- BƯỚC 1: GIỮ GHẾ & RACE CONDITION ---

# 1. Phiên bản hiển nhiên: Kiểm tra rồi mới ghi (Bị lỗi race condition)
def hold_naive(event, seat, user):
    key = f"hold:{event}:{seat}"
    if r.get(key) is None:
        time.sleep(0.01)  # Mở rộng khoảng thời gian trễ để dễ nhìn thấy lỗi
        r.set(key, user, ex=300)
        return True
    return False

# 2. Phiên bản chuẩn: Dùng lệnh nguyên tử SET NX EX
def hold_nx(event, seat, user, ttl=300):
    return bool(r.set(f"hold:{event}:{seat}", user, nx=True, ex=ttl))

if __name__ == "__main__":
    print("=== KIỂM TRA BẢN NAIVE (GET rồi SET) ===")
    r.flushdb()
    wins_naive = race(lambda i: hold_naive("c1", "A1", f"user{i}"))
    print("winners (mong đợi 1, nhưng thực tế sẽ thấy nhiều hơn):", sum(wins_naive))

    print("\n=== KIỂM TRA BẢN NX (Nguyên tử) ===")
    r.flushdb()
    wins_nx = race(lambda i: hold_nx("c1", "A1", f"user{i}"))
    print("winners (luôn luôn là 1):", sum(wins_nx))

HOLD = r.register_script("""
if redis.call('SISMEMBER', KEYS[2], ARGV[2]) == 1 then return 0 end
if redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[3]) then return 1 end
return 0
""")

CONFIRM = r.register_script("""
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('SADD', KEYS[2], ARGV[2])
  redis.call('DEL', KEYS[1])
  return 1
end
return 0
""")

RELEASE = r.register_script("""
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
""")


def hold(event, seat, user, ttl=300):
    return bool(HOLD(keys=[f"hold:{event}:{seat}", f"sold:{event}"], args=[user, seat, ttl]))


def confirm(event, seat, user):
    return bool(CONFIRM(keys=[f"hold:{event}:{seat}", f"sold:{event}"], args=[user, seat]))


def release(event, seat, user):
    return bool(RELEASE(keys=[f"hold:{event}:{seat}"], args=[user]))


def seat_map(event, seats):
    sold = r.smembers(f"sold:{event}")
    return {
        s: "sold" if s in sold else "held" if r.exists(f"hold:{event}:{s}") else "free"
        for s in seats
    }

if __name__ == "__main__":
    print("\n=== KIỂM TRA KỊCH BẢN BƯỚC 2 ===")
    r.flushdb()
    print("linh giữ A1:", hold("c1", "A1", "linh"))      # True
    print("nam giữ A1:", hold("c1", "A1", "nam"))       # False, linh đang giữ
    print("nam xác nhận A1:", confirm("c1", "A1", "nam"))    # False, nam không sở hữu quyền giữ
    print("linh xác nhận A1:", confirm("c1", "A1", "linh"))   # True, đã bán thành công
    print("nam giữ lại A1:", hold("c1", "A1", "nam"))       # False, ghế đã bán
    print("Trạng thái sơ đồ ghế:", seat_map("c1", ["A1", "A2"]))  # {'A1': 'sold', 'A2': 'free'}

# bước 3
def buy_general(event, user):
    left = r.decr(f"stock:{event}")
    if left < 0:
        r.incr(f"stock:{event}")         # undo: nothing was left
        return False
    r.sadd(f"buyers:{event}", user)
    return True

r.flushdb()
r.set("stock:g1", 100)
ok = race(lambda i: buy_general("g1", f"user{i}"), n=500)
print(sum(ok), r.get("stock:g1"), r.scard("buyers:g1"))   # 100 0 100

# --- BƯỚC 4: GIỚI HẠN TẦN SUẤT (RATE LIMITING) ---

# 1. Fixed Window (Cửa sổ cố định)
def allow_fixed(user, limit=5, window=10):
    key = f"rl:fixed:{user}:{int(time.time() // window)}"
    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, window, nx=True)  # Chỉ đặt TTL ở lần đếm đầu tiên
    count, _ = pipe.execute()
    return count <= limit

# 2. Sliding Window (Cửa sổ trượt dùng Lua script)
SLIDING = r.register_script("""
local now, window, limit = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - window)
if redis.call('ZCARD', KEYS[1]) < limit then 
    redis.call('ZADD', KEYS[1], now, ARGV[4]) 
    redis.call('PEXPIRE', KEYS[1], window) 
    return 1
end 
return 0
""")

def allow_sliding(user, limit=5, window_ms=10_000):
    now = int(time.time() * 1000)
    return bool(SLIDING(keys=[f"rl:sliding:{user}"], args=[now, window_ms, limit, uuid.uuid4().hex]))

def try_hold(event, seat, user):
    if not allow_sliding(user):
        return "rate_limited"
    return "ok" if hold(event, seat, user) else "taken"

if __name__ == "__main__":
    print("\n=== KIỂM TRA FIXED WINDOW ===")
    r.flushdb()
    print([allow_fixed("linh") for _ in range(8)])
    # Mong đợi: [True, True, True, True, True, False, False, False]

    print("\n=== KIỂM TRA ĐIỂM YẾU RANH GIỚI CỦA FIXED WINDOW ===")
    r.flushdb()
    # Chờ đến sát thời điểm ranh giới cửa sổ 10 giây
    time.sleep(max(0, 10 - time.time() % 10 - 0.2))
    first = [allow_fixed("bot") for _ in range(5)]
    time.sleep(0.4)  # Chuyển sang cửa sổ tiếp theo
    second = [allow_fixed("bot") for _ in range(5)]
    print("Fixed window (Cho phép cả 10 request vượt giới hạn qua ranh giới):", first + second)
    # Kết quả tất cả đều True (bị lọt lưới bùng nổ ranh giới)

    print("\n=== KIỂM TRA SLIDING WINDOW ===")
    r.flushdb()
    time.sleep(max(0, 10 - time.time() % 10 - 0.2))
    first_sliding = [allow_sliding("bot_pro") for _ in range(5)]
    time.sleep(0.4)
    second_sliding = [allow_sliding("bot_pro") for _ in range(5)]
    print("Sliding window (Đợt thứ hai sẽ bị chặn đứng):", first_sliding + second_sliding)
    # Kết quả đợt hai sẽ chuyển thành False do vượt quá giới hạn trong vòng 10 giây gần nhất

print('BƯỚC 5')
def join_queue(event, user):
    r.zadd(f"queue:{event}", {user: time.time()}, nx=True)   # NX: re-joining keeps your place
    return r.zrank(f"queue:{event}", user) + 1


def admit(event, n):
    return [user for user, _ in r.zpopmin(f"queue:{event}", n)]

for name in ["u1", "u2", "u3"]:
    print(name, join_queue("c1", name))   # positions 1, 2, 3
print(join_queue("c1", "u1"))             # still 1
print(admit("c1", 2))                     # ['u1', 'u2']
print(join_queue("c1", "u3"))             # 1


print('===BƯỚC 6===')
import random

r.flushdb()
SEATS = [f"S{i}" for i in range(1, 21)]

def buyer(i):
    user = f"user{i}"
    for seat in SEATS:
        if hold("c1", seat, user):
            time.sleep(random.random() * 0.05)       # paying
            if confirm("c1", seat, user):
                return seat
    return None

got = [s for s in race(buyer, n=100) if s]
print(len(got), len(set(got)), r.scard("sold:c1"))   # 20 20 20