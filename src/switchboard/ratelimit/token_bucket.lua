-- Atomic token bucket.
-- KEYS[1] bucket   ARGV: capacity, refill_per_s, cost, now_ms, allow_negative (0|1)
-- Returns {allowed, floor(tokens), retry_after_s}
local cap = tonumber(ARGV[1])
local rate = math.max(tonumber(ARGV[2]), 1e-9)
local cost = tonumber(ARGV[3])
local now = tonumber(ARGV[4])

local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1]) or cap
local ts = tonumber(state[2]) or now

tokens = math.min(cap, tokens + math.max(0, now - ts) / 1000 * rate)

local allowed = 0
if tokens >= cost or ARGV[5] == '1' then
  tokens = tokens - cost
  allowed = 1
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(cap / rate * 1000) + 1000)

local retry = 0
if allowed == 0 then
  retry = (cost - tokens) / rate
end
return {allowed, math.floor(tokens), tostring(retry)}
