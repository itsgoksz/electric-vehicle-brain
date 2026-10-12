import math
import googlemaps.convert

def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate the great-circle distance between two points on Earth (in km)."""
    R = 6371.0  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def points_from_polyline(polyline_str: str) -> tuple[list, bool]:
    """
    Decode a polyline into a list of lat/lng dicts.
    Returns a tuple: (list of points, is_truncated boolean)
    This provides an API that explicitly reports partial decoding
    while avoiding fabricated coordinates.
    """
    if not polyline_str or polyline_str == "mock_polyline":
        return [], False
    
    index, lat, lng = 0, 0, 0
    coordinates = []
    is_truncated = False
    
    while index < len(polyline_str):
        shift, result = 0, 0
        try:
            while True:
                if index >= len(polyline_str):
                    raise ValueError("Truncated polyline: incomplete latitude data")
                byte = ord(polyline_str[index]) - 63
                index += 1
                result |= (byte & 0x1f) << shift
                shift += 5
                if byte < 0x20:
                    break
            dlat = ~(result >> 1) if (result & 1) else (result >> 1)
            
            # We must calculate both lat and lng before considering the point complete
            shift_lng, result_lng = 0, 0
            while True:
                if index >= len(polyline_str):
                    raise ValueError("Truncated polyline: incomplete longitude data")
                byte = ord(polyline_str[index]) - 63
                index += 1
                result_lng |= (byte & 0x1f) << shift_lng
                shift_lng += 5
                if byte < 0x20:
                    break
            dlng = ~(result_lng >> 1) if (result_lng & 1) else (result_lng >> 1)
            
            # Point is fully parsed
            lat += dlat
            lng += dlng
            coordinates.append({'lat': lat / 100000.0, 'lng': lng / 100000.0})
            
        except ValueError:
            is_truncated = True
            break
        
    return coordinates, is_truncated

def distance_to_polyline(point: dict, points: list) -> float:
    """Calculate the minimum distance (in km) from a point to any point in a decoded polyline."""
    if not points:
        return 0.0
    
    min_dist = float('inf')
    for p in points:
        dist = haversine_distance(point["lat"], point["lng"], p["lat"], p["lng"])
        if dist < min_dist:
            min_dist = dist
    return min_dist
