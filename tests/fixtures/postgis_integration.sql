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
    cve_met text,
    pobtot integer NOT NULL,
    geometry geometry(Polygon, 6372) NOT NULL
);

INSERT INTO census_2020_mun (cvegeo, cve_met, pobtot, geometry)
VALUES
    (
        '01001',
        '01',
        100,
        ST_GeomFromText('POLYGON ((5 5, 7 5, 7 7, 5 7, 5 5))', 6372)
    ),
    (
        '01002',
        '01',
        200,
        ST_GeomFromText('POLYGON ((25 25, 27 25, 27 27, 25 27, 25 25))', 6372)
    );

CREATE TABLE census_2020_ageb (
    cvegeo text NOT NULL,
    cve_mun text NOT NULL,
    geometry geometry(Polygon, 6372) NOT NULL
);

INSERT INTO census_2020_ageb (cvegeo, cve_mun, geometry)
VALUES
    (
        '0100100010002',
        '01001',
        ST_GeomFromText('POLYGON ((8 8, 9 8, 9 9, 8 9, 8 8))', 6372)
    ),
    (
        '0100100010001',
        '01001',
        ST_GeomFromText('POLYGON ((5 5, 6 5, 6 6, 5 6, 5 5))', 6372)
    );

CREATE TABLE metropoli_2020 (
    cve_met text NOT NULL,
    nom_met text NOT NULL
);

INSERT INTO metropoli_2020 (cve_met, nom_met)
VALUES ('01', 'Aguascalientes');
