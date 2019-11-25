import logging
from followthemoney import model
from banal import hash_data

from aleph.logic.aggregator import get_aggregator
from aleph.queues import queue_task, OP_INDEX
from aleph.model import Mapping
from aleph.index.entities import delete_entities_by_mapping_id

log = logging.getLogger(__name__)


def refresh_mapping(stage, collection, config):
    """Flush all entities generated by a mapping and reload the mapping query
    """
    query = config.get('query')
    mapping_id = config.get('mapping_id')
    mapping = Mapping.by_id(mapping_id)
    delete_entities_by_mapping_id(stage, collection, mapping_id, sync=True)
    config['entity_ids'] = []
    try:
        entity_ids = load_mapping_query(stage, collection, hash_data(query), query)  # noqa
        config['entity_ids'].extend(entity_ids)
    except Exception as exc:
        mapping.set_status(status=Mapping.FAILED, error=str(exc))
    else:
        mapping.set_status(status=Mapping.SUCCESS)
    queue_task(collection, OP_INDEX, job_id=stage.job.id, payload=config)


def load_mapping_query(stage, collection, query_id, query):
    mapping = model.make_mapping(query, key_prefix=collection.foreign_id)
    aggregator = get_aggregator(collection)
    writer = aggregator.bulk()
    entities_count = 0
    proof = query.pop('proof', None)
    entity_ids = []
    for idx, record in enumerate(mapping.source.records, 1):
        for entity in mapping.map(record).values():
            if entity.schema.is_a('Thing'):
                entity.add('proof', proof)
            entity = collection.ns.apply(entity)
            entity_ids.append(entity.id)
            entities_count += 1
            fragment = '%s-%s' % (query_id, idx)
            writer.put(entity, fragment=fragment)

        if idx > 0 and idx % 1000 == 0:
            stage.report_finished(1000)
            log.info("[%s] Loaded %s records, %s entities...",
                     collection.foreign_id,
                     idx, entities_count)
    writer.flush()
    aggregator.close()
    log.info("[%s] Query done (%s entities)",
             collection.foreign_id, entities_count)
    return entity_ids