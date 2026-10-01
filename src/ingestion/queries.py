ISSUES_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    issues(
      first: 50
      states: CLOSED
      after: $cursor
      orderBy: {field: CREATED_AT, direction: ASC}
    ) {
      totalCount
      pageInfo {
        hasNextPage
        endCursor
      }

      nodes {
        number
        title
        body
        createdAt
        closedAt
        state
        url

        comments(first: 50) {
          nodes {
            body
            createdAt
            author {
              login
            }
          }
        }

        timelineItems(
          first: 50
          itemTypes: [CLOSED_EVENT, CROSS_REFERENCED_EVENT]
        ) {
          nodes {
            __typename

            ... on ClosedEvent {
              closer {
                ... on PullRequest {
                  number
                  title
                  url
                }
                ... on Commit {
                  oid
                  url
                }
              }
            }

            ... on CrossReferencedEvent {
              willCloseTarget
              source {
                ... on PullRequest {
                  number
                  title
                  url
                }
                ... on Issue {
                  number
                  title
                  url
                }
              }
            }
          }
        }
      }
    }
  }

  rateLimit {
    cost
    remaining
    limit
  }
}
"""