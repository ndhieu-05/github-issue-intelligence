CREATE CONSTRAINT issue_id IF NOT EXISTS
FOR (i:Issue)
REQUIRE (i.repo, i.number) IS UNIQUE;

CREATE CONSTRAINT pr_id IF NOT EXISTS
FOR (p:PullRequest)
REQUIRE (p.repo, p.number) IS UNIQUE;

CREATE CONSTRAINT commit_id IF NOT EXISTS
FOR (c:Commit)
REQUIRE (c.repo, c.oid) IS UNIQUE;

CREATE INDEX issue_state IF NOT EXISTS
FOR (i:Issue)
ON (i.state);