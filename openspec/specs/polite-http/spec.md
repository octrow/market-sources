## Purpose

Define the one HTTP client every JSON-API adapter sits on: the headers that earn
a 200 from sites that 403 a bare library User-Agent, the pacing and concurrency
cap that keep a burst of `asyncio.gather` from becoming a flood, and — most
importantly — the retry policy, which decides what counts as a hiccup worth
replaying versus an answer to be believed the first time.

Four repos hand-wrote the first three ingredients and none of them had the
fourth. The retry schedule is therefore the part specified in the most detail:
every pacing and backoff decision is driven by an injected `sleep` and `rng`
precisely so that it can be asserted without a clock.

## Requirements

### Requirement: Requests carry browser-mimicking headers

Every request MUST be sent with a real desktop Chrome `user-agent`, an
`accept-language` of `ru-RU,ru;q=0.9,en;q=0.8`, and a JSON-leaning `accept`.
This is not politeness theatre: a bare `python-httpx` User-Agent is 403'd by
every site in scope, so the browser UA is the price of a 200.

Headers layer in three tiers, each overriding the last: library defaults, then
constructor headers, then per-request headers. Header names are matched
case-insensitively, and merging MUST NOT mutate the stored base headers.

#### Scenario: Defaults are sent when the caller sets nothing

- **WHEN** a request is issued on a client constructed with no header overrides
- **THEN** the browser `user-agent`, the `accept-language` and the default
  `accept` are all present on the wire
- **Anchor**: `tests/test_polite_http.py::test_browser_headers_are_sent_by_default`

#### Scenario: Constructor headers override the defaults

- **WHEN** a client is constructed with its own `user_agent` and `headers`
- **THEN** those values replace the defaults and any additional header is sent
  alongside them
- **Anchor**: `tests/test_polite_http.py::test_constructor_headers_and_user_agent_override_defaults`

#### Scenario: Per-request headers beat constructor headers

- **WHEN** a request supplies a header that the constructor already set
- **THEN** the per-request value wins
- **Anchor**: `tests/test_polite_http.py::test_per_request_headers_and_referer_win`

#### Scenario: Merging is case-insensitive and non-destructive

- **WHEN** headers are merged using a different letter case than the stored ones
- **THEN** the merged mapping holds the new value under the lowercased name and
  the client's base headers are left unchanged
- **Anchor**: `tests/test_polite_http.py::test_merge_headers_is_case_insensitive_and_does_not_mutate_base`

### Requirement: Referer is a first-class request argument

`request()` MUST accept `referer` directly rather than requiring callers to
hand-roll the header. A page-matching `referer` is Kaspi's *entire* anti-bot
check, so it is a routine part of issuing a request against these sites, not an
exotic header.

#### Scenario: A supplied referer reaches the wire

- **WHEN** a request passes `referer`
- **THEN** the request carries it as the `referer` header
- **Anchor**: `tests/test_polite_http.py::test_per_request_headers_and_referer_win`

### Requirement: Pacing is per logical request, not per attempt

A single jittered delay drawn uniformly from `min_delay_s..max_delay_s` MUST be
slept once per logical request, inside the concurrency semaphore and before the
first attempt. Retry backoff is additional sleeping layered on top; a retried
request MUST NOT pay the pacing delay again.

Setting `max_delay_s` to zero MUST disable pacing entirely — that is how the
test suite stays fast without stubbing the pacing logic out.

#### Scenario: Each request is preceded by a jittered delay

- **WHEN** two requests are issued on a client pacing between 0.5 s and 1.5 s
- **THEN** each one sleeps a delay drawn from that range before being sent
- **Anchor**: `tests/test_polite_http.py::test_a_jittered_delay_precedes_every_request`

#### Scenario: The delay spans the whole configured range

- **WHEN** the injected random source returns its maximum
- **THEN** the slept delay is the top of the configured range
- **Anchor**: `tests/test_polite_http.py::test_high_jitter_uses_the_top_of_the_range`

#### Scenario: A zero delay skips sleeping altogether

- **WHEN** a client is configured with a zero maximum delay
- **THEN** no pacing sleep occurs at all
- **Anchor**: `tests/test_polite_http.py::test_zero_delay_skips_sleeping_entirely`

### Requirement: The concurrency cap lives in the client

Concurrent in-flight requests MUST NOT exceed `max_concurrency`, which defaults
to 2. The cap belongs to the client rather than to callers because
`asyncio.gather` over forty pages is one line of consumer code away at all
times, and a cap a consumer has to remember is a cap that gets forgotten.

#### Scenario: A burst is throttled to the cap

- **WHEN** six requests are gathered concurrently on a client capped at 2
- **THEN** all six complete and at most two are ever in flight together
- **Anchor**: `tests/test_polite_http.py::test_semaphore_caps_concurrency`

#### Scenario: A cap of one serializes completely

- **WHEN** four requests are gathered concurrently on a client capped at 1
- **THEN** never more than one is in flight
- **Anchor**: `tests/test_polite_http.py::test_max_concurrency_one_serializes`

### Requirement: Only transient failures are retried

Retries MUST be limited to transport errors and to the configured retry statuses
— `429`, `500`, `502`, `503` and `504` by default. Every other status MUST be
raised through `raise_for_status()` on the first attempt without sleeping: a 404
is an answer, not a hiccup, and retrying it only wastes the pacing budget.

`429` MUST be retried despite being a 4xx, and a request that exhausts
`max_retries` MUST surface the underlying error rather than a wrapper.

#### Scenario: A 500 is retried and the retry succeeds

- **WHEN** the server answers 500 then 200
- **THEN** two attempts are made, the successful body is returned, and one
  backoff sleep occurred between them
- **Anchor**: `tests/test_polite_http.py::test_retries_a_500_then_succeeds`

#### Scenario: A 404 is believed immediately

- **WHEN** the server answers 404 on a client allowing three retries
- **THEN** exactly one attempt is made, no sleep occurs, and the status error is
  raised
- **Anchor**: `tests/test_polite_http.py::test_a_404_is_an_answer_not_a_hiccup`

#### Scenario: A 429 is retried even though it is a 4xx

- **WHEN** the server answers 429 then 200
- **THEN** the request is retried and succeeds
- **Anchor**: `tests/test_polite_http.py::test_429_is_retried_even_though_it_is_a_4xx`

#### Scenario: Exhausting the retries raises the status

- **WHEN** the server answers 503 on every one of three allowed attempts
- **THEN** the 503 status error is raised after the third attempt
- **Anchor**: `tests/test_polite_http.py::test_giving_up_after_max_retries_raises_the_status`

#### Scenario: A transport error is retried then re-raised

- **WHEN** every attempt raises a connection error
- **THEN** all allowed attempts are made, each separated by a backoff sleep, and
  the transport error is re-raised
- **Anchor**: `tests/test_polite_http.py::test_transport_errors_are_retried_then_reraised`

#### Scenario: A transport error that clears is recovered

- **WHEN** the first attempt times out and the second succeeds
- **THEN** the successful body is returned
- **Anchor**: `tests/test_polite_http.py::test_a_transport_error_that_recovers`

### Requirement: Backoff is exponential, jittered and capped

The delay before retry `n` (0-based) MUST be `backoff_base_s * 2**n` plus a
random fraction of itself up to `backoff_jitter`, with the exponential base
clamped to `backoff_max_s`. The cap is what keeps a long retry chain from
growing without bound; the jitter is what keeps many clients from retrying in
lockstep.

#### Scenario: The exponential base doubles and then plateaus at the cap

- **WHEN** five retries are needed on a client with a 1 s base and a 4 s cap
- **THEN** the backoff sleeps are 1, 2, 4, 4 and 4 seconds
- **Anchor**: `tests/test_polite_http.py::test_backoff_is_exponential_and_capped`

#### Scenario: Jitter is added on top of the base

- **WHEN** one retry is needed with a 25 % jitter and a random source at its
  maximum
- **THEN** the slept delay is the base plus the full 25 %
- **Anchor**: `tests/test_polite_http.py::test_jitter_is_added_on_top_of_the_exponential_base`

### Requirement: A server-supplied Retry-After wins, but cannot park the process

When a retryable response carries `Retry-After`, that value MUST be used in
place of the computed backoff — the server knows its own recovery window better
than the formula does. It MUST still be clamped to `backoff_max_s`, so a hostile
or mistaken `Retry-After: 86400` cannot park the process for a day.

#### Scenario: Retry-After replaces the computed backoff

- **WHEN** a 429 carries `Retry-After: 7`
- **THEN** the client sleeps seven seconds rather than the formula's delay
- **Anchor**: `tests/test_polite_http.py::test_retry_after_seconds_is_honored_over_backoff`

#### Scenario: An excessive Retry-After is clamped

- **WHEN** a 503 carries `Retry-After: 600` on a client capped at 30 s
- **THEN** the client sleeps 30 seconds
- **Anchor**: `tests/test_polite_http.py::test_retry_after_is_clamped_to_backoff_max`

#### Scenario: An HTTP-date Retry-After is honored

- **WHEN** a 503 carries a `Retry-After` HTTP-date a few seconds in the future
- **THEN** the client sleeps approximately the remaining interval
- **Anchor**: `tests/test_polite_http.py::test_retry_after_http_date_is_honored`

### Requirement: Retry-After parsing accepts both forms and never goes negative

`parse_retry_after` MUST accept delta-seconds and HTTP-dates alike, returning
seconds as a float, and MUST return `None` for a missing, empty or unparseable
value. It MUST never return a negative number: a date already in the past means
"retry now", i.e. `0.0`. The reference instant MUST be injectable so the
date branch is testable without a clock.

#### Scenario: Scalar and unparseable values

- **WHEN** the header is absent, empty, a plain number, a padded number, a
  negative number, or unparseable text
- **THEN** the result is respectively `None`, `None`, the number, the number,
  `0.0`, and `None`
- **Anchor**: `tests/test_polite_http.py::test_parse_retry_after_scalars`

#### Scenario: A date in the past means retry now

- **WHEN** the header is an HTTP-date long past
- **THEN** the parsed delay is `0.0` rather than a negative number
- **Anchor**: `tests/test_polite_http.py::test_parse_retry_after_http_date_in_the_past_is_zero`

#### Scenario: The reference instant is injectable

- **WHEN** an HTTP-date is parsed against an explicitly supplied "now"
- **THEN** the delay is measured from that instant, not from the wall clock
- **Anchor**: `tests/test_polite_http.py::test_parse_retry_after_http_date_uses_the_injected_now`

### Requirement: Retries never replay a side effect unless invited

Retrying MUST be enabled by default only for methods that are idempotent under
RFC 9110 — `GET`, `HEAD`, `OPTIONS`, `PUT`, `DELETE`, `TRACE`. `POST` and
`PATCH` MUST NOT be retried by default, because replaying one can duplicate a
side effect the client cannot see.

A caller who knows a particular non-idempotent call is really a read MUST be
able to opt it back in with `retry=True` on that one call — Kaspi's offers
endpoint is a POST in name only. The opt-in is per call, never a client-wide
switch, and `retry=False` MUST likewise opt a single idempotent call out.

#### Scenario: A POST is not retried by default

- **WHEN** a POST receives a 500 on a client allowing three retries
- **THEN** exactly one attempt is made and the status error is raised
- **Anchor**: `tests/test_polite_http.py::test_post_is_not_retried_by_default`

#### Scenario: A caller can opt one POST into retrying

- **WHEN** a POST is issued with `retry=True` and the server answers 500 then 200
- **THEN** the request is retried and succeeds
- **Anchor**: `tests/test_polite_http.py::test_post_is_retried_when_the_caller_opts_in`

#### Scenario: A caller can opt one GET out of retrying

- **WHEN** a GET is issued with `retry=False` and receives a 500
- **THEN** exactly one attempt is made
- **Anchor**: `tests/test_polite_http.py::test_get_can_opt_out_of_retries`

### Requirement: One connection pool, created lazily and reused

The underlying `httpx.AsyncClient` MUST be created on first use and reused
across every subsequent request, rather than built per request as all four
original implementations did — that pattern discarded the connection pool on
every call. `aclose()` MUST release it and be safe to call more than once, and
the client MUST also work as an async context manager.

#### Scenario: The same client instance serves repeated requests

- **WHEN** two requests are issued and then the client is closed
- **THEN** both used the same underlying client object, and closing clears it
- **Anchor**: `tests/test_polite_http.py::test_the_http_client_is_reused_across_requests`

#### Scenario: The context manager closes the pool on exit

- **WHEN** a client is used as an async context manager
- **THEN** the underlying client is released when the block exits
- **Anchor**: `tests/test_polite_http.py::test_usable_as_an_async_context_manager`

### Requirement: Transport, proxy and timeout are configurable

Timeout, redirect-following and an optional proxy MUST be passed through to the
underlying client. The proxy exists because a datacenter IP may be throttled
where a residential one is not. An empty proxy string MUST be treated as no
proxy, so that reading an unset environment variable is harmless.

An injected transport MUST take precedence over the proxy, which is what lets
the whole client be exercised without a network.

#### Scenario: Proxy and timeout reach the underlying client

- **WHEN** a client is constructed with a proxy URL and a custom timeout
- **THEN** both are passed to the underlying client along with redirect-following
- **Anchor**: `tests/test_polite_http.py::test_proxy_is_passed_to_the_underlying_client`

#### Scenario: An empty proxy string is no proxy

- **WHEN** a client is constructed with an empty proxy string
- **THEN** the client reports no proxy
- **Anchor**: `tests/test_polite_http.py::test_empty_proxy_string_means_no_proxy`

### Requirement: Incoherent configuration is rejected at construction

Construction MUST raise `ValueError` when `min_delay_s` exceeds `max_delay_s` or
when `max_retries` is negative. Both would otherwise fail confusingly much later,
at the first request, far from the line that caused them.

#### Scenario: An inverted delay range and a negative retry count are refused

- **WHEN** a client is constructed with a minimum delay above its maximum, and
  separately with a negative retry count
- **THEN** each construction raises `ValueError`
- **Anchor**: `tests/test_polite_http.py::test_bad_construction_is_rejected`
