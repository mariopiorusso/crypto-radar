"""Local X retention/deletion handling. Makes no network or paid API requests."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import re

from .db import connect


def prune(conn, now):
    count=conn.execute('DELETE FROM x_posts WHERE expires_ts<=?',(now.isoformat(),)).rowcount
    conn.commit()
    return count


def delete_posts(conn, post_ids):
    if any(not re.fullmatch(r'\d{1,30}',value) for value in post_ids):
        raise ValueError('Numeric post IDs required')
    count=0
    for value in post_ids:
        count+=conn.execute('DELETE FROM x_posts WHERE post_id=?',(value,)).rowcount
    conn.commit()
    return count


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True)
    parser.add_argument('--delete-post',action='append',default=[])
    args=parser.parse_args()
    with closing(connect(args.db)) as conn:
        print({'expired_posts_deleted':prune(conn,datetime.now(timezone.utc)),
               'requested_posts_deleted':delete_posts(conn,args.delete_post)})


if __name__=='__main__':
    main()
