CREATE TABLE denue_2025_11 (
    per_ocu text NOT NULL,
    codigo_act text NOT NULL,
    geometry geometry(Point, 6372) NOT NULL
);

INSERT INTO denue_2025_11 (per_ocu, codigo_act, geometry)
VALUES
    ('0 a 5 personas', '111111', ST_GeomFromText('POINT (1 1)', 6372)),
    ('51 a 100 personas', '222222', ST_GeomFromText('POINT (20 20)', 6372));

CREATE TABLE mesh_level_9 (
    codigo text NOT NULL,
    geometry geometry(Polygon, 6372) NOT NULL
);

INSERT INTO mesh_level_9 (codigo, geometry)
VALUES
    (
        'mesh-inside',
        ST_GeomFromText('POLYGON ((2 2, 4 2, 4 4, 2 4, 2 2))', 6372)
    ),
    (
        'mesh-outside',
        ST_GeomFromText('POLYGON ((20 20, 22 20, 22 22, 20 22, 20 20))', 6372)
    );

CREATE TABLE census_2020_mun (
    cvegeo text NOT NULL,
    pobtot integer NOT NULL,
    geometry geometry(Polygon, 6372) NOT NULL
);

INSERT INTO census_2020_mun (cvegeo, pobtot, geometry)
VALUES
    (
        '01001',
        100,
        ST_GeomFromText('POLYGON ((5 5, 7 5, 7 7, 5 7, 5 5))', 6372)
    ),
    (
        '01002',
        200,
        ST_GeomFromText('POLYGON ((25 25, 27 25, 27 27, 25 27, 25 25))', 6372)
    );
