import json
from typing import Any, Dict, List, Optional

import morecantile
from buildpg import Func
from buildpg import asyncpg, clauses, render

from timvt.errors import (
    MissingEPSGCode,
)
from timvt.settings import TileSettings
from timvt.layer import Function
from fastapi import BackgroundTasks

tile_settings = TileSettings()


class StoredFunction(Function):
    type: str = "StoredFunction"

    async def get_tile_from_cache(self, conn, tile, tms):
        """Get Tile Data from cache."""
        # Get the tile from the tile_cache.tile table
        sql_query = clauses.Select(
            clauses.Column("tile"),
            from_="tile_cache.tile",
            where=(
                clauses.Column("x")
                == tile.x & clauses.Column("y")
                == tile.y & clauses.Column("z")
                == tile.z & clauses.Column("tms")
                == tms.identifier & clauses.Column("layer")
                == self.id
            ),
        )
        q, p = render(
            str(sql_query),
            x=tile.x,
            y=tile.y,
            z=tile.z,
            tms=tms.identifier,
            layer=self.id,
        )

        return await conn.fetchval(q, *p)

    async def get_tile(
        self,
        background_tasks: BackgroundTasks,
        pool: asyncpg.BuildPgPool,
        tile: morecantile.Tile,
        tms: morecantile.TileMatrixSet,
        **kwargs: Any,
    ):
        """Get Tile Data."""
        # We only support TMS with valid EPSG code
        if not tms.crs.to_epsg():
            raise MissingEPSGCode(
                f"{tms.identifier}'s CRS does not have a valid EPSG code."
            )

        async with pool.acquire() as conn:
            transaction = conn.transaction()
            await transaction.start()

            # Check the cache for the tile
            content = await self.get_tile_from_cache(conn, tile, tms)
            if content:
                return content

            # Build the query
            sql_query = clauses.Select(
                Func(
                    self.function_name,
                    ":x",
                    ":y",
                    ":z",
                    ":query_params::text::json",
                ),
            )
            q, p = render(
                str(sql_query),
                x=tile.x,
                y=tile.y,
                z=tile.z,
                query_params=json.dumps(kwargs),
            )

            # execute the query
            content = await conn.fetchval(q, *p)

            use_cache = True
            if use_cache:
                background_tasks.add_task(
                    self.set_cached_tile, src_path, x, y, z, content
                )

            # rollback
            await transaction.rollback()

        return content
