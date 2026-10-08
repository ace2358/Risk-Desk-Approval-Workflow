from workflow_engine.schemas import MockUser


MOCK_USERS = (
    MockUser(id="submitter", name="Jordan Lee", role="Submitter"),
    MockUser(id="engineer", name="Sam Rivera", role="Engineer"),
    MockUser(id="manager", name="Morgan Chen", role="Manager"),
    MockUser(id="final", name="Alex Patel", role="Final reviewer"),
    MockUser(id="observer", name="Taylor Quinn", role="Observer"),
)