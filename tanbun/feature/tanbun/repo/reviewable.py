"""ゲーム候補と閲覧記録で共有する現行・所有単文の条件."""

from .cypher import q_location


def q_reviewable_sentences() -> str:
    """sentence_ids、user_id、任意のresource_idを受け取る."""
    return (
        """
        MATCH (sentence:Sentence)
        WHERE sentence.uid IN $sentence_ids
        MATCH (resource:Resource {uid: sentence.resource_uid})
        WHERE ($resource_id IS NULL OR resource.uid = $resource_id)
          AND (EXISTS {
            MATCH (resource)-[:PARENT*0..]->()-[:OWNED]->(:User {uid:$user_id})
          } OR EXISTS {
            MATCH (:User {uid:$user_id})-[:PLAYS_DUNGEON]->(resource)
          })
        WITH DISTINCT sentence
        """
        + q_location("sentence")
        + """
        WITH DISTINCT sentence, location
        WHERE location IS NOT NULL
        """
    )
