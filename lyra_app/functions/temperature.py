import ee


def get_landsat_cloud_mask(image: ee.image.Image) -> ee.image.Image:
    """Calculates the cloud mask for a Landsat image.

    Parameters
    ----------
    image : ee.Image
        The image to analyze. Must have valid cloud bands.

    Returns
    -------
    ee.Image
        The resultant cloud mask image with binary values. A 0 indicates that a
        cloud was present.
    """

    qa = image.select("QA_PIXEL")

    dilated_cloud_bit = 1
    cloud_bit = 3
    cloud_shadow_bit = 4

    mask = qa.bitwiseAnd(1 << dilated_cloud_bit).eq(0)
    mask = mask.And(qa.bitwiseAnd(1 << cloud_bit).eq(0))
    mask = mask.And(qa.bitwiseAnd(1 << cloud_shadow_bit).eq(0))

    return image.updateMask(mask)


def reduce_landsat_collection(
    bounds: ee.geometry.Geometry,
    start_date: str,
    end_date: str,
) -> ee.image.Image:
    filtered: ee.imagecollection.ImageCollection = (
        ee.imagecollection.ImageCollection("LANDSAT/LC09/C02/T1_L2")
        .filterDate(start_date, end_date)
        .filterBounds(bounds)
    )

    if filtered.size().getInfo() == 0:
        err = "No measurements for given date and location found."
        raise ValueError(err)

    return (
        filtered.map(get_landsat_cloud_mask)
        .select("ST_B10")
        .mean()
        .multiply(0.00341802)
        .add(149 - 273.15)
        .clip(bounds)
    )
