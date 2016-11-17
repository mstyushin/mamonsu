# -*- coding: utf-8 -*-

from mamonsu.plugins.pgsql.plugin import PgsqlPlugin as Plugin
from .pool import Pooler


class Cfs(Plugin):

    Interval = 5 * 60

    DEFAULT_CONFIG = {'force_enable': str(False)}

    compressed_ratio_sql = """
select
    n.nspname || '.' || c.relname as table_name,
    cfs_compression_ratio(c.oid::regclass) as ratio,
    (pg_catalog.pg_total_relation_size(c.oid::regclass) - pg_catalog.pg_indexes_size(c.oid::regclass)) as compressed_size
from
    pg_catalog.pg_class as c
    left join pg_catalog.pg_namespace n on n.oid = c.relnamespace
where c.reltablespace in (select oid from pg_catalog.pg_tablespace where spcoptions::text ~ 'compression')
    and c.relkind IN ('r','v','m','S','f','')

union all

select
    n.nspname || '.' || c.relname as table_name,
    cfs_compression_ratio(c.oid::regclass) as ratio,
    pg_catalog.pg_total_relation_size(c.oid::regclass) as compressed_size -- pg_toast included
from
    pg_catalog.pg_class as c
    left join pg_catalog.pg_namespace n on n.oid = c.relnamespace
where c.reltablespace in (select oid from pg_catalog.pg_tablespace where spcoptions::text ~ 'compression')
    and c.relkind = 'i' and n.nspname <> 'pg_toast';
"""

    activity_sql = """
select
    cfs_gc_activity_processed_bytes(), -- writed
    cfs_gc_activity_processed_pages() * 8 * 1024, -- scanned
    cfs_gc_activity_processed_files(), -- writed
    cfs_gc_activity_scanned_files(); -- scanned
"""

    prev = {}

    def run(self, zbx):

        if self.plugin_config('force_enable') == 'False':
            self.disable_and_exit_if_not_pgpro_ee()

        relations, compressed_size, non_compressed_size = [], 0, 0
        for db in Pooler.databases:
            for row in Pooler.query(self.compressed_ratio_sql, db):
                relation_name = '{0}.{1}'.format(db, row[0])
                relations.append({'{#COMPRESSED_RELATION}': relation_name})
                compressed_size += row[2]
                non_compressed_size += row[2] * row[1]
                zbx.send('pgsql.cfs.relation[{0}]'.format(row[0]), row[1])
        zbx.send('pgsql.cfs.discovery_compressed_relations[]', zbx.json({'data': relations}))
        zbx.send('pgsql.cfs.activity[total_compress_ratio]', non_compressed_size / compressed_size)
        del(relations, compressed_size, non_compressed_size)

        info = Pooler.query(self.activity_sql)[0]
        zbx.send('pgsql.cfs.activity[writed_bytes]', info[0], delta=self.DELTA_SPEED, only_positive_speed=True)
        zbx.send('pgsql.cfs.activity[scanned_bytes]', info[1], delta=self.DELTA_SPEED, only_positive_speed=True)

        # calculate current compress ratio
        if ('writed_bytes' in self.prev) and ('scanned_bytes' in self.prev):
            if info[0] > self.prev['writed_bytes'] and info[1] > self.prev['scanned_bytes']:
                val = (self.prev['scanned_bytes'] - info[1]) / ((self.prev['writed_bytes'] - info[0]) * self.Interval)
                zbx.send('pgsql.cfs.activity[current_compress_ratio]', val)
        self.prev['writed_bytes'] = info[0]
        self.prev['scanned_bytes'] = info[1]

        zbx.send('pgsql.cfs.activity[compressed_files]', info[2], delta=self.DELTA_SPEED, only_positive_speed=True)
        zbx.send('pgsql.cfs.activity[scanned_files]', info[3], delta=self.DELTA_SPEED, only_positive_speed=True)

    def items(self, template):
        return template.item({
            'name': 'PostgreSQL cfs compression: Writed byte/s',
            'key': 'pgsql.cfs.activity[writed_bytes]',
            'delay': self.Interval
        }) + template.item({
            'name': 'PostgreSQL cfs compression: Scanned byte/s',
            'key': 'pgsql.cfs.activity[scanned_bytes]',
            'delay': self.Interval
        }) + template.item({
            'name': 'PostgreSQL cfs compression: compressed files/s',
            'key': 'pgsql.cfs.activity[compressed_files]',
            'delay': self.Interval
        }) + template.item({
            'name': 'PostgreSQL cfs compression: scanned files/s',
            'key': 'pgsql.cfs.activity[scanned_files]',
            'delay': self.Interval
        }) + template.item({
            'name': 'PostgreSQL cfs compression: current ratio',
            'key': 'pgsql.cfs.activity[current_compress_ratio]',
            'delay': self.Interval
        }) + template.item({
            'name': 'PostgreSQL cfs compression: total ratio',
            'key': 'pgsql.cfs.activity[total_compress_ratio]',
            'delay': self.Interval
        })

    def graphs(self, template):
        result = template.graph({
            'name': 'PostgreSQL cfs compression: current ratio',
            'items': [{
                'key': 'pgsql.cfs.activity[current_compress_ratio]',
                'color': '00CC00'
            }]
        })
        result += template.graph({
            'name': 'PostgreSQL cfs compression: compressed files',
            'items': [{
                'key': 'pgsql.cfs.activity[compressed_files]',
                'color': '00CC00'
            }]
        })
        result += template.graph({
            'name': 'PostgreSQL cfs compression: writed bytes',
            'items': [{
                'key': 'pgsql.cfs.activity[writed_bytes]',
                'color': '00CC00'
            }]
        })
        return result

    def discovery_rules(self, template):
        rule = {
            'name': 'Compressed relations discovery',
            'key': 'pgsql.cfs.discovery_compressed_relations[]',
            'filter': '{#COMPRESSED_RELATION}:.*'
        }
        items = [
            {'key': 'pgsql.cfs.relation[{#COMPRESSED_RELATION}]',
                'name': 'Relation {#COMPRESSED_RELATION}: compression ratio',
                'delay': self.Interval}
        ]
        graphs = [
            {
                'name': 'Relation {#COMPRESSED_RELATION}: compression ratio',
                'delay': self.Interval,
                'items': [
                    {'color': '00CC00',
                        'key': 'pgsql.cfs.relation[{#COMPRESSED_RELATION}]'}]
            },
        ]
        return template.discovery_rule(rule=rule, items=items, graphs=graphs)
