from urllib.parse import urljoin
import logging  # pylint: disable=C0302
from flask import redirect
from flask_restx import Resource, Namespace, fields, reqparse
from flask_restx import resource
from src.queries.get_tracks import get_tracks
from src.queries.get_track_user_creator_node import get_track_user_creator_node
from src.api.v1.helpers import abort_not_found, decode_with_abort,  \
    extend_track, make_response, search_parser, extend_user, get_default_max, \
    trending_parser, full_trending_parser, success_response, abort_bad_request_param, to_dict, \
    format_offset, format_limit, decode_string_id
from .models.tracks import track, track_full
from src.queries.search_queries import SearchKind, search
from src.queries.get_trending_tracks import get_trending_tracks
from flask.json import dumps
from src.utils.redis_cache import cache, extract_key, use_redis_cache
from flask.globals import request
from src.utils.redis_metrics import record_metrics
from src.api.v1.models.users import user_model_full
from src.queries.get_reposters_for_track import get_reposters_for_track
from src.queries.get_savers_for_track import get_savers_for_track

logger = logging.getLogger(__name__)

# Models & namespaces

ns = Namespace('tracks', description='Track related operations')
full_ns = Namespace('tracks', description='Full track operations')

track_response = make_response("track_response", ns, fields.Nested(track))
full_track_response = make_response("full_track_response", full_ns, fields.Nested(track_full))

tracks_response = make_response(
    "tracks_response", ns, fields.List(fields.Nested(track)))
full_tracks_response = make_response(
    "full_tracks_response", full_ns, fields.List(fields.Nested(track_full))
)

# Get single track

def get_single_track(track_id, endpoint_ns):
    decoded_id = decode_with_abort(track_id, endpoint_ns)
    args = {"id": [decoded_id], "with_users": True, "filter_deleted": True}
    tracks = get_tracks(args)
    if not tracks:
        abort_not_found(track_id, endpoint_ns)
    single_track = extend_track(tracks[0])
    return success_response(single_track)

TRACK_ROUTE = '/<string:track_id>'
@ns.route(TRACK_ROUTE)
class Track(Resource):
    @record_metrics
    @ns.doc(
        id="""Get Track""",
        params={'track_id': 'A Track ID'},
        responses={
            200: 'Success',
            400: 'Bad request',
            500: 'Server error'
        }
    )
    @ns.marshal_with(track_response)
    @cache(ttl_sec=5)
    def get(self, track_id):
        """Fetch a track."""
        return get_single_track(track_id, ns)

@full_ns.route(TRACK_ROUTE)
class FullTrack(Resource):
    @record_metrics
    @full_ns.marshal_with(full_track_response)
    @cache(ttl_sec=5)
    def get(self, track_id):
        return get_single_track(track_id, full_ns)

# Stream

def tranform_stream_cache(stream_url):
    return redirect(stream_url)

@ns.route("/<string:track_id>/stream")
class TrackStream(Resource):
    @record_metrics
    @ns.doc(
        id="""Stream Track""",
        params={'track_id': 'A Track ID'},
        responses={
            200: 'Success',
            216: 'Partial content',
            400: 'Bad request',
            416: 'Content range invalid',
            500: 'Server error'
        }
    )
    @cache(ttl_sec=5, transform=tranform_stream_cache)
    def get(self, track_id):
        """
        Get the track's streamable mp3 file.

        This endpoint accepts the Range header for streaming.
        https://developer.mozilla.org/en-US/docs/Web/HTTP/Range_requests
        """
        decoded_id = decode_with_abort(track_id, ns)
        args = {"track_id": decoded_id}
        creator_nodes = get_track_user_creator_node(args)
        if creator_nodes is None:
            abort_not_found(track_id, ns)
        creator_nodes = creator_nodes.split(',')
        if not creator_nodes:
            abort_not_found(track_id, ns)

        primary_node = creator_nodes[0]
        stream_url = urljoin(primary_node, 'tracks/stream/{}'.format(track_id))

        return stream_url

track_search_result = make_response(
    "track_search", ns, fields.List(fields.Nested(track)))

@ns.route("/search")
class TrackSearchResult(Resource):
    @record_metrics
    @ns.doc(
        id="""Search Tracks""",
        params={
            'query': 'Search Query',
            'only_downloadable': 'Return only downloadable tracks'
        },
        responses={
            200: 'Success',
            400: 'Bad request',
            500: 'Server error'
        }
    )
    @ns.marshal_with(track_search_result)
    @ns.expect(search_parser)
    @cache(ttl_sec=60)
    def get(self):
        """Search for a track."""
        args = search_parser.parse_args()
        query = args["query"]
        if not query:
            abort_bad_request_param('query', ns)
        search_args = {
            "query": query,
            "kind": SearchKind.tracks.name,
            "is_auto_complete": False,
            "current_user_id": None,
            "with_users": True,
            "limit": 10,
            "offset": 0,
            "only_downloadable": args["only_downloadable"]
        }
        response = search(search_args)
        tracks = response["tracks"]
        tracks = list(map(extend_track, tracks))
        return success_response(tracks)


# Trending
#
# There are two trending endpoints - regular and full. Regular
# uses the familiar caching decorator, while full is more interesting.
#
# Full Trending is consumed page by page in the client, but we'd like to avoid caching
# each page seperately (to avoid old pages interleaving with new ones on the client).
# We're further constrained by the need to fetch more than the page size of ~10 in our playcount
# query in order to score + sort the tracks.
#
# We address this by always fetching and scoring `TRENDING_LIMIT` (>> page limit) tracks,
# caching the entire tracks list. This cached value is sliced by limit + offset and returned.
# This cache entry is be keyed by genre + user_id + time_range.
#
# However, this causes an issue where every distinct user_id (every logged in user) will have a cache miss
# on their first call to trending. We deal with this by adding an additional layer of caching inside
# `get_trending_tracks.py`, which caches the scored tracks before they are populated (keyed by genre + time).
# With this second cache, each user_id can reuse on the same cached list of tracks, and then populate them uniquely.

TRENDING_LIMIT = 100
TRENDING_TTL_SEC = 30 * 60

def get_trending(args):
    """Get Trending, shared between full and regular endpoints."""
    # construct args
    time = args.get("time") if args.get("time") is not None else 'week'
    current_user_id = args.get("user_id")
    args = {
        'time': time,
        'genre': args.get("genre", None),
        'with_users': True,
        'limit': TRENDING_LIMIT,
        'offset': 0
    }

    # decode and add user_id if necessary
    if current_user_id:
        decoded_id = decode_string_id(current_user_id)
        args["current_user_id"] = decoded_id

    tracks = get_trending_tracks(args)
    return list(map(extend_track, tracks))

@ns.route("/trending")
class Trending(Resource):
    @record_metrics
    @ns.doc(
        id="""Trending Tracks""",
        params={
            'genre': 'Trending tracks for a specified genre',
            'time': 'Trending tracks over a specified time range (week, month, allTime)'
        },
        responses={
            200: 'Success',
            400: 'Bad request',
            500: 'Server error'
        }
    )
    @ns.marshal_with(tracks_response)
    @cache(ttl_sec=TRENDING_TTL_SEC)
    def get(self):
        """Gets the top 100 trending (most popular) tracks on Audius"""
        args = trending_parser.parse_args()
        trending = get_trending(args)
        return success_response(trending)


@full_ns.route("/trending")
class FullTrending(Resource):
    def get_cache_key(self):
        """Construct a cache key from genre + user + time"""
        request_items = to_dict(request.args)
        request_items.pop('limit', None)
        request_items.pop('offset', None)
        key = extract_key(request.path, request_items.items())
        return key

    @record_metrics
    @full_ns.marshal_with(full_tracks_response)
    def get(self):
        args = full_trending_parser.parse_args()
        offset = format_offset(args)
        limit = format_limit(args, TRENDING_LIMIT)
        key = self.get_cache_key()

        # Attempt to use the cached tracks list
        full_trending = use_redis_cache(key, TRENDING_TTL_SEC, lambda: get_trending(args))
        trending = full_trending[offset: limit + offset]
        return success_response(trending)

track_favorites_route_parser = reqparse.RequestParser()
track_favorites_route_parser.add_argument('user_id', required=False)
track_favorites_route_parser.add_argument('limit', required=False, type=int)
track_favorites_route_parser.add_argument('offset', required=False, type=int)
track_favorites_response = make_response("following_response", full_ns, fields.List(fields.Nested(user_model_full)))
@full_ns.route("/<string:track_id>/favorites")
class FullTrackFavorites(Resource):
    @full_ns.expect(track_favorites_route_parser)
    @full_ns.doc(
        id="""Get Users that Favorited a Track""",
        params={
            'user_id': 'A User ID',
            'limit': 'Limit',
            'offset': 'Offset'
        },
        responses={
            200: 'Success',
            400: 'Bad request',
            500: 'Server error'
        }
    )
    @full_ns.marshal_with(track_favorites_response)
    @cache(ttl_sec=5)
    def get(self, track_id):
        args = track_favorites_route_parser.parse_args()
        decoded_id = decode_with_abort(track_id, full_ns)
        limit = get_default_max(args.get('limit'), 10, 100)
        offset = get_default_max(args.get('offset'), 0)

        current_user_id = None
        if args.get("user_id"):
            current_user_id = decode_string_id(args["user_id"])
        args = {
            'save_track_id': decoded_id,
            'current_user_id': current_user_id,
            'limit': limit,
            'offset': offset
        }
        users = get_savers_for_track(args)
        users = list(map(extend_user, users))

        return success_response(users)

track_reposts_route_parser = reqparse.RequestParser()
track_reposts_route_parser.add_argument('user_id', required=False)
track_reposts_route_parser.add_argument('limit', required=False, type=int)
track_reposts_route_parser.add_argument('offset', required=False, type=int)
track_reposts_response = make_response("following_response", full_ns, fields.List(fields.Nested(user_model_full)))
@full_ns.route("/<string:track_id>/reposts")
class FullTrackReposts(Resource):
    @full_ns.expect(track_reposts_route_parser)
    @full_ns.doc(
        id="""Get Users that Reposted a Track""",
        params={
            'user_id': 'A User ID',
            'limit': 'Limit',
            'offset': 'Offset'
        },
        responses={
            200: 'Success',
            400: 'Bad request',
            500: 'Server error'
        }
    )
    @full_ns.marshal_with(track_reposts_response)
    @cache(ttl_sec=5)
    def get(self, track_id):
        args = track_reposts_route_parser.parse_args()
        decoded_id = decode_with_abort(track_id, full_ns)
        limit = get_default_max(args.get('limit'), 10, 100)
        offset = get_default_max(args.get('offset'), 0)

        current_user_id = None
        if args.get("user_id"):
            current_user_id = decode_string_id(args["user_id"])
        args = {
            'repost_track_id': decoded_id,
            'current_user_id': current_user_id,
            'limit': limit,
            'offset': offset
        }
        users = get_reposters_for_track(args)
        users = list(map(extend_user, users))
        return success_response(users)
