import logging
import os
import random
import signal

import gevent
import requests
from locust import HttpUser, between, events
from locust.exception import StopUser

API_ROOT = os.getenv("LOADTEST_API_ROOT", "/api")
USERNAME = os.getenv("LOADTEST_USER", "Admin")
PASSWORD = os.getenv("LOADTEST_PASSWORD", "admin123")
FAIL_RATIO = float(os.getenv("LOADTEST_FAIL_RATIO", "0.01"))
P95_MS = os.getenv("LOADTEST_P95_MS", "")
SAMPLE_SIZE = int(os.getenv("LOADTEST_SAMPLE_SIZE", "200"))
SMOKE = os.getenv("LOADTEST_SMOKE") == "1"

logger = logging.getLogger(__name__)

LOGIN = """mutation($username: String!, $password: String!) {
  tokenAuth(username: $username, password: $password) { refreshExpiresIn }
}"""

CSRF = "mutation { getCsrfToken { csrfToken } }"

PAGE = 100  # the largest page the server allows on a connection

DISCOVER = """query($first: Int, $after: String) {
  claims(first: $first, after: $after, orderBy: ["-dateClaimed"]) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      uuid
      insuree { chfId }
      healthFacility { location { uuid parent { uuid } } }
    } }
  }
}"""

CLAIMS = """query($parent: String, $location: String, $first: Int) {
  claims(healthFacility_Location_Parent_Uuid: $parent, healthFacility_Location_Uuid: $location,
         orderBy: ["-dateClaimed"], first: $first) {
    totalCount
    pageInfo { hasNextPage hasPreviousPage startCursor endCursor }
    edges { node {
      uuid code jsonExt dateClaimed feedbackStatus reviewStatus claimed approved status
      healthFacility { id uuid name code }
      insuree { id uuid chfId lastName otherNames }
      attachmentsCount
    } }
  }
}"""

CLAIM = """query($uuid: UUID) {
  claim(uuid: $uuid) {
    uuid code status dateFrom dateTo dateClaimed claimed approved
    icd { code name }
    insuree { chfId lastName otherNames }
    healthFacility { code name }
    items { item { code name } qtyProvided priceAsked status }
    services { service { code name } qtyProvided priceAsked status }
  }
}"""

INSUREE = """query($chfId: String) {
  insurees(chfId: $chfId) {
    edges { node {
      id uuid chfId lastName otherNames dob age validityFrom validityTo
      family { id }
      gender { code gender altLanguage }
      healthFacility {
        id uuid code name level
        location { id uuid code name parent { id uuid code name } }
      }
    } }
  }
}"""

POLICIES = """query($chfId: String!) {
  policiesByInsuree(chfId: $chfId, orderBy: "expiryDate", activeOrLastExpiredOnly: true, first: 5) {
    totalCount
    edges { node {
      policyUuid productCode productName officerCode officerName enrollDate effectiveDate
      startDate expiryDate status policyValue balance ded dedInPatient dedOutPatient
      ceiling ceilingInPatient ceilingOutPatient
    } }
  }
}"""

PREMIUMS = """query($policyUuids: [String]!) {
  premiumsByPolicies(policyUuids: $policyUuids, orderBy: "-payDate", first: 5) {
    totalCount
    edges { node { id uuid payDate amount payType receipt isPhotoFee payer { id uuid name } } }
  }
}"""

PRODUCTS = """query($first: Int) {
  products(first: $first) {
    totalCount
    edges { node { uuid code name dateFrom dateTo maxMembers lumpSum } }
  }
}"""


def log_in(client, post):
    """The browser's login: a JWT cookie, a server-side session holding the CSRF token,
    and that token echoed in X-CSRFToken on every request.

    Both cookies are issued with the Secure flag, so a client following cookie rules
    would not send them back over plain HTTP. They are attached as an explicit header
    instead, which works on HTTP and HTTPS alike.
    """
    login = post({"query": LOGIN, "variables": {"username": USERNAME, "password": PASSWORD}}, "tokenAuth")
    cookies = dict(login.cookies)
    if "JWT" not in cookies:
        raise RuntimeError(f"login as {USERNAME} failed: HTTP {login.status_code} {login.text[:200]}")
    _send_cookies(client, cookies)
    csrf = post({"query": CSRF}, "getCsrfToken")
    cookies.update(csrf.cookies)
    _send_cookies(client, cookies)
    client.headers["X-CSRFToken"] = csrf.json()["data"]["getCsrfToken"]["csrfToken"]


def _send_cookies(client, cookies):
    client.cookies.clear()
    client.headers["Cookie"] = "; ".join(f"{name}={value}" for name, value in cookies.items())


def _facility_location(node):
    location = (node.get("healthFacility") or {}).get("location") or {}
    return (location.get("parent") or {}).get("uuid"), location.get("uuid")


class Sample:
    """Claims, their insurees and their health facilities' locations, read once per run from
    whatever the target holds, so the scenarios carry no identifiers of a particular server."""

    claims = []
    chf_ids = []
    locations = []

    @classmethod
    def load(cls, host):
        url = f"{host}{API_ROOT}/graphql"
        with requests.Session() as client:
            client.headers["Content-Type"] = "application/json"
            log_in(client, lambda body, _name: client.post(url, json=body, timeout=120))
            nodes, after = [], None
            while len(nodes) < SAMPLE_SIZE:
                variables = {"first": min(PAGE, SAMPLE_SIZE - len(nodes)), "after": after}
                reply = client.post(url, json={"query": DISCOVER, "variables": variables}, timeout=300).json()
                if reply.get("errors"):
                    raise RuntimeError(f"discovery failed: {reply['errors']}")
                page = reply["data"]["claims"]
                nodes += [edge["node"] for edge in page["edges"]]
                if not page["pageInfo"]["hasNextPage"]:
                    break
                after = page["pageInfo"]["endCursor"]
        cls.claims = [n["uuid"] for n in nodes]
        cls.chf_ids = sorted({n["insuree"]["chfId"] for n in nodes if n.get("insuree")})
        cls.locations = sorted({loc for loc in map(_facility_location, nodes) if all(loc)})
        if not (cls.claims and cls.chf_ids and cls.locations):
            raise RuntimeError("discovery found no claims to sample: is the target seeded?")
        logger.info("sample: %d claims, %d insurees, %d facility locations",
                    len(cls.claims), len(cls.chf_ids), len(cls.locations))


@events.test_start.add_listener
def _discover(environment, **_):
    # Locust logs an exception raised here and starts the users anyway, and runner.quit()
    # issued now is undone by the spawn that follows; SIGTERM takes Locust's normal shutdown.
    try:
        Sample.load(environment.host)
    except Exception:
        logger.exception("discovery failed, stopping")
        environment.discovery_failed = True
        gevent.spawn_later(0, os.kill, os.getpid(), signal.SIGTERM)


@events.quitting.add_listener
def _thresholds(environment, **_):
    if getattr(environment, "discovery_failed", False):
        environment.process_exit_code = 1
        return
    total = environment.stats.total
    if total.num_requests == 0:
        logger.error("no requests were made")
        environment.process_exit_code = 1
        return
    reasons = []
    # Setting process_exit_code overrides Locust's own exit-on-error, so errors in users are counted here.
    crashes = sum(e["count"] for e in environment.runner.exceptions.values())
    if crashes:
        reasons.append(f"{crashes} errors in simulated users")
    if total.fail_ratio > FAIL_RATIO:
        reasons.append(f"fail ratio {total.fail_ratio:.2%} > {FAIL_RATIO:.2%}")
    if P95_MS and float(P95_MS) > 0:
        p95 = total.get_response_time_percentile(0.95)
        if p95 > float(P95_MS):
            reasons.append(f"p95 {p95:.0f} ms > {float(P95_MS):.0f} ms")
    for reason in reasons:
        logger.error("threshold exceeded: %s", reason)
    environment.process_exit_code = 1 if reasons else 0


class HealthFinancingUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        self.client.headers["Content-Type"] = "application/json"
        try:
            log_in(self.client, lambda body, name: self.client.post(f"{API_ROOT}/graphql", json=body, name=name))
        except Exception as error:
            # Locust drops a user whose on_start raises without recording it; report it so the run fails.
            self.environment.events.user_error.fire(user_instance=self, exception=error, tb=error.__traceback__)
            raise StopUser()

    def graphql(self, name, query, variables):
        with self.client.post(
            f"{API_ROOT}/graphql",
            json={"query": query, "variables": variables},
            name=name,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return None
            try:
                body = response.json()
            except ValueError:
                response.failure("response is not JSON")
                return None
            if body.get("errors"):
                response.failure(str(body["errors"])[:300])
                return None
            return body.get("data")

    def current_user(self):
        self.client.get(f"{API_ROOT}/core/users/current_user/", name="current_user")

    def claims_by_location(self):
        parent, location = random.choice(Sample.locations)
        first = random.choice((10, 20, 50))
        self.graphql(f"claims first:{first}", CLAIMS, {"parent": parent, "location": location, "first": first})

    def claim_detail(self):
        self.graphql("claim", CLAIM, {"uuid": random.choice(Sample.claims)})

    def eligibility(self):
        chf_id = random.choice(Sample.chf_ids)
        if self.graphql("insurees", INSUREE, {"chfId": chf_id}) is None:
            return
        data = self.graphql("policiesByInsuree", POLICIES, {"chfId": chf_id})
        if not data:
            return
        uuids = [edge["node"]["policyUuid"] for edge in data["policiesByInsuree"]["edges"]]
        if uuids:
            self.graphql("premiumsByPolicies", PREMIUMS, {"policyUuids": uuids})

    def products(self):
        self.graphql("products", PRODUCTS, {"first": random.choice((10, 20))})

    def every_scenario_once(self):
        for scenario in self.SCENARIOS:
            scenario(self)
        self.environment.runner.quit()

    SCENARIOS = {current_user: 1, claims_by_location: 5, claim_detail: 3, eligibility: 4, products: 1}
    tasks = [every_scenario_once] if SMOKE else SCENARIOS
