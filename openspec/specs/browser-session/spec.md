## Purpose

Define the long-lived headless Chromium session that sits under any source whose
data cannot be reached over plain HTTP: how a page is launched lazily and
reused, why every navigation queues behind a single lock, how a session that has
gone stale is recovered, and when an idle browser closes itself.

The module generalizes machinery that existed in two prior implementations, only
one of which got it right. Its rules are mostly scar tissue: navigating a shared
page concurrently is a correctness bug rather than a speedup, blocking asset
requests turns a working session into a 403, and an idle timer that cancels
itself mid-teardown leaks a browser process. Each rule below is specified with
the failure it prevents, because each one looks removable without it.

## Requirements

### Requirement: Importing the library MUST NOT require playwright

`playwright` MUST be imported inside the launch path, never at module scope, so
that `market_sources` and every module under it import cleanly on a machine
where only the base dependencies are installed. The `browser` extra is the price
of *running* a browser-backed source, never of importing one — a consumer who
only wants the HTTP-backed sources installs nothing extra.

A session driven through an injected `page_factory` MUST therefore work end to
end with playwright entirely absent.

#### Scenario: The import block used by these tests genuinely bites

- **WHEN** the test fixture installs a meta-path finder rejecting `playwright`
- **THEN** importing `playwright` raises `ImportError`
- **Anchor**: `tests/test_playwright_session.py::test_playwright_is_really_blocked`

#### Scenario: Every module imports with playwright unavailable

- **WHEN** `market_sources`, `market_sources.playwright_session` and
  `market_sources.ozon.source` are imported with `playwright` blocked
- **THEN** all three import successfully
- **Anchor**: `tests/test_playwright_session.py::test_modules_import_without_playwright`

#### Scenario: An injected page drives a full session with playwright absent

- **WHEN** a session built on an injected `page_factory` fetches a URL with
  `playwright` blocked
- **THEN** the navigation completes and returns the page body
- **Anchor**: `tests/test_playwright_session.py::test_a_session_runs_without_playwright_when_a_page_is_injected`

### Requirement: One page is opened lazily and reused

Constructing a session MUST NOT launch anything. The browser, context and page
are created on first navigation and then reused for every subsequent one, so a
process pays the multi-hundred-megabyte cost of Chromium only if it actually
navigates. The configured navigation timeout MUST be applied to each navigation.

#### Scenario: Two navigations share one page

- **WHEN** two URLs are fetched in sequence
- **THEN** both return the page body and only one page was ever opened, having
  received both navigations
- **Anchor**: `tests/test_playwright_session.py::test_fetch_text_returns_the_body_and_reuses_the_page`

#### Scenario: The configured navigation timeout is applied

- **WHEN** a session is constructed with a custom navigation timeout
- **THEN** that timeout is passed to the navigation
- **Anchor**: `tests/test_playwright_session.py::test_nav_timeout_is_passed_through`

### Requirement: Every navigation is serialized behind one lock

Navigations MUST run one at a time. A session owns a single page, so every
navigation mutates state shared by all of them — cookies, history, and the DOM
the response is read off. Concurrency here is a correctness bug, not a speedup:
two overlapping navigations race to define what "the current page" means, and
the loser reads the other's document.

#### Scenario: Concurrent fetches do not interleave

- **WHEN** three fetches are issued concurrently on one session
- **THEN** each navigation runs to completion before the next begins
- **Anchor**: `tests/test_playwright_session.py::test_navigations_are_serialized_behind_the_lock`

### Requirement: Arbitrary page work runs under the same guarantees

`page_op` MUST navigate and then hand the live page to a caller-supplied
coroutine under exactly the guarantees `fetch_text` gets — the same lock, idle
reset, status classification and relaunch retry — with the page handed over only
after a successful navigation. Consumers needing locator-based extraction rather
than body text MUST NOT have to reimplement the state machine to get it.

#### Scenario: The callback receives the live page

- **WHEN** `page_op` is called with a coroutine reading the page title
- **THEN** the navigation happens and the callback's return value is returned
- **Anchor**: `tests/test_playwright_session.py::test_page_op_hands_the_live_page_to_the_callback`

#### Scenario: The callback path retries a stale session too

- **WHEN** `page_op`'s first navigation returns a relaunch status
- **THEN** the session relaunches and the callback runs against the fresh page
- **Anchor**: `tests/test_playwright_session.py::test_page_op_retries_relaunch_statuses_like_fetch_text`

### Requirement: The ready hook runs once per page and its success is cached

`prepare_page` MUST run exactly once per freshly opened page, inside the
navigation lock, and its success MUST be remembered until the session is torn
down. It is the hook for whatever makes a page usable — passing an anti-bot
challenge, logging in, accepting a cookie banner — and those are expensive
enough that repeating them per navigation would defeat the point of a persistent
session.

A hook that raises MUST propagate rather than leaving a half-ready session in
use.

#### Scenario: The hook runs per page, not per navigation

- **WHEN** two URLs are fetched, then the session is closed and a third is
  fetched
- **THEN** the hook ran once for the first page and once more for the page
  opened after teardown
- **Anchor**: `tests/test_playwright_session.py::test_prepare_page_runs_once_per_page`

#### Scenario: A failing ready hook propagates

- **WHEN** the hook raises because the page never became usable
- **THEN** that error surfaces to the caller
- **Anchor**: `tests/test_playwright_session.py::test_a_failing_ready_hook_propagates`

### Requirement: A stale session is recovered by relaunch, not by retrying in place

A navigation answering with a status in `relaunch_statuses` — `403` and `307` by
default — MUST tear the session down and retry once on a wholly fresh page.
These statuses mean the cookie jar is dead rather than "no such page", and
nothing short of a new browser, context and page fixes that; retrying the same
navigation on the same page just repeats the rejection.

Retry MUST be bounded, defaulting to one attempt, and MUST be disableable per
call. A relaunch status that survives the retry MUST raise.

#### Scenario: A relaunch status tears down and succeeds on a fresh page

- **WHEN** the first navigation returns 403 and the retry succeeds
- **THEN** the fresh page's body is returned, two pages were opened, and the
  stale one was closed
- **Anchor**: `tests/test_playwright_session.py::test_a_relaunch_status_tears_down_and_retries_once`

#### Scenario: A relaunch status that never clears gives up

- **WHEN** every navigation returns 403
- **THEN** the session retries once and then raises reporting the status
- **Anchor**: `tests/test_playwright_session.py::test_a_relaunch_status_that_never_clears_raises`

#### Scenario: Retrying can be switched off for one call

- **WHEN** a fetch requesting no retries hits a relaunch status
- **THEN** it raises immediately, having opened only one page
- **Anchor**: `tests/test_playwright_session.py::test_retries_can_be_disabled_per_call`

### Requirement: Statuses a relaunch cannot fix raise immediately

Any non-200 outside `relaunch_statuses` MUST raise the session's `error_class`
without a retry — a 404 is an answer, and relaunching a browser to ask again
costs seconds to learn the same thing. A navigation yielding no response object
at all MUST be reported as status `-1` rather than crashing on the missing
value.

The error type MUST be overridable per subclass, so a site-specific session can
raise an error carrying its own identity without wrapping.

#### Scenario: A 404 is not retried

- **WHEN** a navigation returns 404
- **THEN** the session error is raised naming the status, with one page opened
- **Anchor**: `tests/test_playwright_session.py::test_a_plain_error_status_is_not_retried`

#### Scenario: A missing response object is reported as -1

- **WHEN** a navigation completes without producing a response object
- **THEN** the session error is raised naming status -1
- **Anchor**: `tests/test_playwright_session.py::test_no_response_object_is_reported_as_status_minus_one`

### Requirement: A dead browser is detected by message, not by exception type

An error whose message indicates a closed, crashed or disconnected browser MUST
trigger the same relaunch-and-retry as a stale-session status. Classification
MUST be message-based rather than requiring a `playwright` exception instance —
that is what lets the whole state machine be exercised against an injected fake
page, and what keeps the check working when the driver's exception types change.

Errors that do not look like a dead browser MUST propagate untouched: a bad
selector is a bug in the caller, and relaunching Chromium will not fix it.

#### Scenario: Closed, crashed and unrelated errors are classified

- **WHEN** the classifier is given a "browser has been closed" error, a
  "crashed" error, and an unrelated `ValueError`
- **THEN** the first two are treated as dead-browser errors and the third is not
- **Anchor**: `tests/test_playwright_session.py::test_is_dead_browser_error_classification`

#### Scenario: A dead browser is revived by relaunch

- **WHEN** the first navigation raises a closed-browser error
- **THEN** the session relaunches and the retry returns the fresh page's body
- **Anchor**: `tests/test_playwright_session.py::test_a_dead_browser_error_triggers_a_relaunch`

#### Scenario: An unrelated error is not retried

- **WHEN** a navigation raises an error unrelated to browser liveness
- **THEN** it propagates unchanged, with only one page opened
- **Anchor**: `tests/test_playwright_session.py::test_an_unrelated_error_is_not_retried`

### Requirement: Page assets MUST NOT be route-blocked

The session MUST NOT block images, stylesheets or fonts — not through launch
flags, and not by installing a request route that aborts them. Blocking them is
the obvious optimization and it is wrong: anti-bot challenges load their assets
through exactly those request types, and a challenge that cannot load its assets
answers 403 instead of admitting the session.

This is the most expensive lesson encoded in this module and the reason the
default launch arguments contain no blocking. A future change that adds
asset-blocking to make navigation faster will appear to work locally and fail
against the live sites.

#### Scenario: The default launch configuration blocks no asset types

- **WHEN** a session is launched with the default arguments
- **THEN** no launch flag and no request route suppresses images, stylesheets or
  fonts

### Requirement: Teardown is idempotent and the session relaunches lazily

`aclose()` MUST release the page, context, browser and driver, MUST be safe to
call repeatedly, and MUST leave the session usable — the next navigation
relaunches from scratch. Teardown MUST take the navigation lock, so it can never
pull the page out from under an in-flight navigation, and it MUST be best-effort:
a failure closing one handle MUST NOT prevent the rest from being released.

#### Scenario: Closing twice is safe and the next call relaunches

- **WHEN** a session is fetched from, closed twice, then fetched from again
- **THEN** the first page was closed and a second page was opened for the later
  fetch
- **Anchor**: `tests/test_playwright_session.py::test_aclose_is_idempotent_and_relaunches_lazily`

### Requirement: An idle session closes itself and re-arms on use

A session left unused for `idle_timeout_s` MUST tear itself down, because an
idle headless Chromium costs roughly 300 MB for nothing; the next navigation
relaunches lazily. Each navigation MUST cancel and re-arm the timer, so an
active session is never closed underneath its own caller.

The idle task MUST detach itself before tearing down and MUST take the
navigation lock to do it. Both are load-bearing: the original cancelled itself
and *then* awaited the teardown, so the resulting cancellation landed mid-close
and left the browser process alive, and it tore down without the lock, so it
could yank the page out from under an in-flight navigation.

An `idle_timeout_s` of zero MUST disable the timer entirely.

#### Scenario: The session survives until the timeout elapses

- **WHEN** a session with a 600 s idle timeout is used and the timer is then
  allowed to fire
- **THEN** the timer was armed for 600 s, the page stayed open until it fired,
  and the session was released afterwards
- **Anchor**: `tests/test_playwright_session.py::test_the_idle_timer_closes_the_session_only_after_the_timeout`

#### Scenario: A new navigation re-arms the timer

- **WHEN** a second fetch happens while the first idle timer is still pending
- **THEN** the first timer is cancelled, a fresh one is armed, and the page stays
  open
- **Anchor**: `tests/test_playwright_session.py::test_a_new_request_resets_the_idle_timer`

#### Scenario: A zero timeout arms nothing

- **WHEN** a session is configured with a zero idle timeout and used
- **THEN** no idle sleep is scheduled and no idle task exists
- **Anchor**: `tests/test_playwright_session.py::test_a_zero_idle_timeout_schedules_nothing`
