import math
import os
import random

import keyboard
import pygame
import win32api
import win32con
import win32gui


# ------------------------------------------------------------
# Sökvägar
# ------------------------------------------------------------

from pathlib import Path

GAME_FOLDER = Path(__file__).resolve().parent
ASSETS_FOLDER = GAME_FOLDER / "Assets"

CAR_FOLDER = ASSETS_FOLDER / "Car"
CONE_PATH = ASSETS_FOLDER / "Misc" / "Cone.png"
WALL_PATH = ASSETS_FOLDER / "Wall" / "Wall1.png"



# ------------------------------------------------------------
# Inställningar
# ------------------------------------------------------------

CAR_WIDTH = 91
CAR_LENGTH = 102

CAR_HITBOX_WIDTH = 38
CAR_HITBOX_LENGTH = 60

CONE_SIZE = 20
CONE_RADIUS = 0.5

WALL_TILE_SIZE = 18
WALL_OVERLAP = 2
WALL_STEP = WALL_TILE_SIZE - WALL_OVERLAP

WALL_COLLISION_THICKNESS = 10


MAX_SPEED = 8.0
ACCELERATION = 0.1
BRAKE_FORCE = 0.3
COASTING_DECELERATION = 0.02
BACKWARD_MAX_SPEED = -3.0
STEER_SPEED = 2.5

BASE_DRIFT_FACTOR = 0.88
DRIFT_SPEED_LOSS = 0.05

CONE_MAX_SPEED = 5.0
CONE_DECELERATION = 0.2

TRACK_COLOR = (0, 0, 0)
SMOKE_COLOR = (220, 220, 220)
TRANSPARENT_COLOR = (0, 0, 1)

# En liten marginal hindrar att bilen fastnar precis i väggen.
COLLISION_EPSILON = 0.75


# ------------------------------------------------------------
# Hjälpfunktioner
# ------------------------------------------------------------

def load_image(path):
    """Ladda bild med alfa och visa ett tydligt fel om den saknas."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Hittar inte bildfilen:\n{path}")

    return pygame.image.load(path).convert_alpha()


def crop_transparent_margins(image):
    """Beskär bort helt genomskinliga kanter runt en bild."""
    alpha = image.get_alpha()

    if alpha is None:
        return image.copy()

    bounds = image.get_bounding_rect(min_alpha=1)

    if bounds.width == 0 or bounds.height == 0:
        return image.copy()

    return image.subsurface(bounds).copy()


def mouse_window_position(hwnd):
    """Omvandla musens skärmposition till fönstrets klientposition."""
    screen_pos = win32api.GetCursorPos()
    client_pos = win32gui.ScreenToClient(hwnd, screen_pos)
    return pygame.math.Vector2(client_pos)


def rectangle_axes(angle_degrees):
    """Returnera bilens framåt- och sidriktning."""
    angle = math.radians(angle_degrees)

    forward = pygame.math.Vector2(
        math.cos(angle),
        -math.sin(angle)
    )
    side = pygame.math.Vector2(
        math.sin(angle),
        math.cos(angle)
    )

    return forward, side


def rectangles_overlap(center_a, angle_a, half_a, center_b, angle_b, half_b):
    """
    SAT-kollisionstest mellan två roterade rektanglar.

    Returnerar:
        (False, None, 0) om de inte kolliderar
        (True, normal, överlappning) om de kolliderar
    """
    forward_a, side_a = rectangle_axes(angle_a)
    forward_b, side_b = rectangle_axes(angle_b)

    axes = (forward_a, side_a, forward_b, side_b)
    center_difference = center_b - center_a

    smallest_overlap = float("inf")
    smallest_axis = None

    for axis in axes:
        radius_a = (
            half_a[0] * abs(forward_a.dot(axis))
            + half_a[1] * abs(side_a.dot(axis))
        )
        radius_b = (
            half_b[0] * abs(forward_b.dot(axis))
            + half_b[1] * abs(side_b.dot(axis))
        )

        distance = abs(center_difference.dot(axis))
        overlap = radius_a + radius_b - distance

        if overlap <= 0:
            return False, None, 0

        if overlap < smallest_overlap:
            smallest_overlap = overlap
            smallest_axis = axis

    # Normalen ska peka från objekt B mot bilen A.
    if center_difference.dot(smallest_axis) > 0:
        smallest_axis = -smallest_axis

    return True, smallest_axis, smallest_overlap


def circle_rectangle_collision(circle_center, circle_radius, rect_center,
                               rect_angle, rect_half_length, rect_half_width):
    """
    Kollision mellan cirkel och roterad rektangel.

    Returnerar (normal, penetration) vid kollision, annars None.
    Normalen pekar ut från rektangeln mot cirkeln.
    """
    forward, side = rectangle_axes(rect_angle)
    offset = circle_center - rect_center

    local_forward = offset.dot(forward)
    local_side = offset.dot(side)

    closest_forward = max(
        -rect_half_length,
        min(local_forward, rect_half_length)
    )
    closest_side = max(
        -rect_half_width,
        min(local_side, rect_half_width)
    )

    closest_point = (
        rect_center
        + forward * closest_forward
        + side * closest_side
    )

    difference = circle_center - closest_point
    distance = difference.length()

    if distance >= circle_radius:
        return None

    if distance > 0.0001:
        normal = difference / distance
        penetration = circle_radius - distance
        return normal, penetration

    # Cirkelns mitt ligger inne i bilrektangeln.
    # Välj den närmaste sidan att flytta den mot.
    forward_depth = rect_half_length - abs(local_forward)
    side_depth = rect_half_width - abs(local_side)

    if forward_depth < side_depth:
        sign = 1 if local_forward >= 0 else -1
        normal = forward * sign
        penetration = circle_radius + forward_depth
    else:
        sign = 1 if local_side >= 0 else -1
        normal = side * sign
        penetration = circle_radius + side_depth

    return normal, penetration


def create_wall(start, end, wall_tile_image):
    """
    Skapa en vägg:
    - visuella plattor placeras längs linjen
    - kollisionen lagras som en enda rektangel
    """
    vector = end - start
    length = vector.length()

    if length < 1:
        return None

    direction = vector.normalize()
    angle = math.degrees(math.atan2(vector.y, vector.x))

    # Bilden är kvadratisk efter beskärning och skalning.
    # En enda rektangel för hela väggen undviker tile-kollisioner.
    half_thickness = WALL_COLLISION_THICKNESS / 2


    tile_count = max(1, math.ceil(length / WALL_STEP) + 1)
    visual_tiles = []

    rotated_image = pygame.transform.rotate(wall_tile_image, -angle)

    for index in range(tile_count):
        distance = min(index * WALL_STEP, length)
        center = start + direction * distance

        rect = rotated_image.get_rect(
            center=(round(center.x), round(center.y))
        )

        visual_tiles.append({
            "image": rotated_image,
            "rect": rect
        })

    return {
        "center": (start + end) / 2,
        "angle": angle,
        "half_length": length / 2,
        "half_thickness": half_thickness,
        "tiles": visual_tiles
    }


def resolve_wall_collisions(car_center, car_angle, car_half_size, walls):
    """
    Flytta bilen ut ur väggar och ta bort hastighet in mot väggen.
    Flera pass hanterar hörn där två väggar möts.
    """
    for _ in range(4):
        any_collision = False

        for wall in walls:
            wall_center = pygame.math.Vector2(wall["center"])
            wall_half_size = (
                wall["half_length"],
                wall["half_thickness"]
            )

            collides, normal, penetration = rectangles_overlap(
                car_center,
                car_angle,
                car_half_size,
                wall_center,
                wall["angle"],
                wall_half_size
            )

            if not collides:
                continue

            any_collision = True

            # Flytta precis utanför väggen.
            car_center += normal * (penetration + COLLISION_EPSILON)

            # Bromsa rörelse in mot väggen men låt bilen glida längs den.
            forward, _ = rectangle_axes(car_angle)
            velocity = forward * current_speed

            velocity_into_wall = velocity.dot(normal)

            if velocity_into_wall < 0:
                velocity -= normal * velocity_into_wall

                # Översätt återstående fart till bilens framåtriktning.
                current_speed_value = velocity.dot(forward)

                if abs(current_speed_value) < 0.08:
                    current_speed_value = 0.0

                return car_center, current_speed_value, True

        if not any_collision:
            break

    return car_center, current_speed, False


def reset_game():
    """Återställ bilen och ta bort alla placerade objekt och effekter."""
    global x, y, speed, angle, travel_angle, wall_start

    x = SCREEN_WIDTH / 2
    y = SCREEN_HEIGHT / 2
    speed = 0.0
    angle = 0.0
    travel_angle = 0.0
    wall_start = None

    cones.clear()
    walls.clear()
    left_track_points.clear()
    right_track_points.clear()
    smoke_particles.clear()


# ------------------------------------------------------------
# Starta Pygame och skapa fönster
# ------------------------------------------------------------

pygame.init()

display_info = pygame.display.Info()
SCREEN_WIDTH = display_info.current_w
SCREEN_HEIGHT = display_info.current_h

screen = pygame.display.set_mode(
    (SCREEN_WIDTH, SCREEN_HEIGHT),
    pygame.NOFRAME
)
pygame.display.set_caption("Drift Car")

hwnd = pygame.display.get_wm_info()["window"]

# Borderless, transparent färgnyckel och alltid överst.
styles = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
win32gui.SetWindowLong(
    hwnd,
    win32con.GWL_EXSTYLE,
    styles | win32con.WS_EX_LAYERED | win32con.WS_EX_TOPMOST
)
win32gui.SetLayeredWindowAttributes(
    hwnd,
    win32api.RGB(*TRANSPARENT_COLOR),
    0,
    win32con.LWA_COLORKEY
)


# ------------------------------------------------------------
# Ladda bilder
# ------------------------------------------------------------

car_sprites = [
    load_image(os.path.join(CAR_FOLDER, f"Red{i}.png"))
    for i in range(1, 9)
]

# Bilbilderna skalas till storleken som används i bilens kollision.
car_sprites = [
    pygame.transform.smoothscale(sprite, (CAR_WIDTH, CAR_LENGTH))
    for sprite in car_sprites
]

cone_image = pygame.transform.smoothscale(
    load_image(CONE_PATH),
    (CONE_SIZE, CONE_SIZE)
)

# Viktigt: beskära väggbildens transparenta kanter FÖRE skalning.
wall_source = crop_transparent_margins(load_image(WALL_PATH))
wall_tile_image = pygame.transform.smoothscale(
    wall_source,
    (WALL_TILE_SIZE, WALL_TILE_SIZE)
)


# ------------------------------------------------------------
# Speltillstånd
# ------------------------------------------------------------

cones = []
walls = []
wall_start = None

x = SCREEN_WIDTH / 2
y = SCREEN_HEIGHT / 2
speed = 0.0
angle = 0.0
travel_angle = 0.0

left_track_points = []
right_track_points = []
smoke_particles = []

MAX_TRACK_LEN = 30

clock = pygame.time.Clock()
running = True


# ------------------------------------------------------------
# Huvudloop
# ------------------------------------------------------------

try:
    while running:
        # Händelser och engångsknappar.
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False

                elif event.key == pygame.K_0:
                    reset_game()

                elif event.key == pygame.K_1:
                    position = mouse_window_position(hwnd)
                    cone_rect = cone_image.get_rect(
                        center=(round(position.x), round(position.y))
                    )
                    cones.append({
                        "rect": cone_rect,
                        "velocity": pygame.math.Vector2()
                    })

                elif event.key == pygame.K_2:
                    position = mouse_window_position(hwnd)

                    if wall_start is None:
                        wall_start = position
                    else:
                        wall = create_wall(
                            wall_start,
                            position,
                            wall_tile_image
                        )

                        if wall is not None:
                            walls.append(wall)

                        wall_start = None

        if keyboard.is_pressed("esc"):
            running = False

        # Styrning.
        if keyboard.is_pressed("left"):
            angle += STEER_SPEED

        if keyboard.is_pressed("right"):
            angle -= STEER_SPEED

        # Gas, broms och rullning.
        if keyboard.is_pressed("up"):
            speed = min(speed + ACCELERATION, MAX_SPEED)

        elif keyboard.is_pressed("down"):
            if speed > 0:
                speed = max(speed - BRAKE_FORCE, 0)
            else:
                speed = max(speed - ACCELERATION, BACKWARD_MAX_SPEED)

        elif speed > 0:
            speed = max(speed - COASTING_DECELERATION, 0)

        elif speed < 0:
            speed = min(speed + COASTING_DECELERATION, 0)

        # Drift.
        angle_difference = abs(
            (angle - travel_angle + 180) % 360 - 180
        )

        if speed > 3 and angle_difference > 15:
            drift_factor = min(
                0.96,
                BASE_DRIFT_FACTOR + angle_difference / 500
            )
            speed = max(1.0, speed - DRIFT_SPEED_LOSS)
        else:
            drift_factor = BASE_DRIFT_FACTOR

        forward_x = math.cos(math.radians(angle))
        forward_y = -math.sin(math.radians(angle))

        if speed != 0:
            travel_x = math.cos(math.radians(travel_angle))
            travel_y = -math.sin(math.radians(travel_angle))

            new_x = travel_x * drift_factor + forward_x * (1 - drift_factor)
            new_y = travel_y * drift_factor + forward_y * (1 - drift_factor)

            travel_angle = math.degrees(math.atan2(-new_y, new_x))
        else:
            travel_angle = angle

        # Flytta bilen.
        travel_radians = math.radians(travel_angle)
        x += speed * math.cos(travel_radians)
        y -= speed * math.sin(travel_radians)

        car_center = pygame.math.Vector2(x, y)
        car_half_size = (
            CAR_HITBOX_LENGTH / 2,
            CAR_HITBOX_WIDTH / 2
        )


        # Väggkollision. Vägglistan innehåller en rektangel per hel vägg,
        # inte en separat kollision för varje bildplatta.
        current_speed = speed

        for _ in range(4):
            car_center, current_speed, hit_wall = resolve_wall_collisions(
                car_center,
                angle,
                car_half_size,
                walls
            )

            if not hit_wall:
                break

        x, y = car_center.x, car_center.y
        speed = current_speed

        # Håll bilen inom skärmen.
        if x < 0 or x > SCREEN_WIDTH or y < 0 or y > SCREEN_HEIGHT:
            x = max(0, min(x, SCREEN_WIDTH))
            y = max(0, min(y, SCREEN_HEIGHT))
            left_track_points.clear()
            right_track_points.clear()

        # Flytta koner och bromsa deras hastighet.
        for cone in cones:
            cone["rect"].centerx += round(cone["velocity"].x)
            cone["rect"].centery += round(cone["velocity"].y)

            cone_speed = cone["velocity"].length()

            if cone_speed > 0:
                new_cone_speed = max(0, cone_speed - CONE_DECELERATION)

                if new_cone_speed == 0:
                    cone["velocity"].update(0, 0)
                else:
                    cone["velocity"].scale_to_length(new_cone_speed)

        # Bil- och konkollision.
        car_center = pygame.math.Vector2(x, y)

        for cone in cones:
            cone_center = pygame.math.Vector2(cone["rect"].center)

            collision = circle_rectangle_collision(
                cone_center,
                CONE_RADIUS,
                car_center,
                angle,
                CAR_HITBOX_LENGTH / 2,
                CAR_HITBOX_WIDTH / 2

            )

            if collision is None:
                continue

            push_direction, penetration = collision

            # Flytta konen ut ur bilen direkt, så den inte fastnar
            # och ger en ny kollision varje bildruta.
            cone_center += push_direction * (penetration + 1.0)
            cone["rect"].center = (
                round(cone_center.x),
                round(cone_center.y)
            )

            impact_speed = min(abs(speed), MAX_SPEED)
            knockback = min(
                CONE_MAX_SPEED,
                1.0 + impact_speed * 0.4
            )

            side_direction = pygame.math.Vector2(
                -push_direction.y,
                push_direction.x
            )
            side_amount = random.uniform(-0.8, 0.8)

            cone["velocity"] = (
                push_direction * knockback
                + side_direction * side_amount
            )

            if cone["velocity"].length() > CONE_MAX_SPEED:
                cone["velocity"].scale_to_length(CONE_MAX_SPEED)

            # Knuffa tillbaka bilen och minska farten.
            car_center -= push_direction * 2.0
            speed *= 0.8

        x, y = car_center.x, car_center.y

        # Hjulens positioner.
        angle_radians = math.radians(angle)
        back_offset = -20
        side_offset = 8

        wheel_left = (
            x + back_offset * math.cos(angle_radians)
              - side_offset * math.sin(angle_radians),
            y - (
                back_offset * math.sin(angle_radians)
                + side_offset * math.cos(angle_radians)
            )
        )

        wheel_right = (
            x + back_offset * math.cos(angle_radians)
              + side_offset * math.sin(angle_radians),
            y - (
                back_offset * math.sin(angle_radians)
                - side_offset * math.cos(angle_radians)
            )
        )

        # Hjulspår och rök.
        if abs(speed) > 0.1:
            left_track_points.append(wheel_left)
            right_track_points.append(wheel_right)

            if speed > 3 and angle_difference > 20:
                for wheel_x, wheel_y in (wheel_left, wheel_right):
                    if random.random() < 0.5:
                        smoke_particles.append([
                            wheel_x + random.uniform(-2, 2),
                            wheel_y + random.uniform(-2, 2),
                            random.uniform(3, 6),
                            random.uniform(60, 130)
                        ])
        else:
            left_track_points.append(None)
            right_track_points.append(None)

        if len(left_track_points) > MAX_TRACK_LEN:
            left_track_points.pop(0)
            right_track_points.pop(0)

        # Uppdatera rökpartiklar.
        for particle in smoke_particles[:]:
            particle[2] += 0.25
            particle[3] -= 5

            if particle[3] <= 0:
                smoke_particles.remove(particle)

        # Rita transparent bakgrund.
        screen.fill(TRANSPARENT_COLOR)

        # Hjulspår.
        track_surface = pygame.Surface(
            (SCREEN_WIDTH, SCREEN_HEIGHT),
            pygame.SRCALPHA
        )

        for track in (left_track_points, right_track_points):
            for index in range(1, len(track)):
                point_a = track[index - 1]
                point_b = track[index]

                if point_a is not None and point_b is not None:
                    alpha = int(index / len(track) * 140)
                    pygame.draw.line(
                        track_surface,
                        (*TRACK_COLOR, alpha),
                        point_a,
                        point_b,
                        4
                    )

        screen.blit(track_surface, (0, 0))

        # Rök.
        smoke_surface = pygame.Surface(
            (SCREEN_WIDTH, SCREEN_HEIGHT),
            pygame.SRCALPHA
        )

        for particle in smoke_particles:
            alpha = max(0, min(255, int(particle[3])))
            pygame.draw.circle(
                smoke_surface,
                (*SMOKE_COLOR, alpha),
                (int(particle[0]), int(particle[1])),
                int(particle[2])
            )

        screen.blit(smoke_surface, (0, 0))

        # Rita väggplattor.
        for wall in walls:
            for tile in wall["tiles"]:
                screen.blit(tile["image"], tile["rect"])

        # Rita koner.
        for cone in cones:
            screen.blit(cone_image, cone["rect"])

        # Välj och rotera bilsprite.
        normalized_angle = angle % 360
        sprite_index = round(normalized_angle / 45) % 8
        sprite_angle = sprite_index * 45

        rotation_difference = (
            (normalized_angle - sprite_angle + 180) % 360 - 180
        )

        rotated_car = pygame.transform.rotate(
            car_sprites[sprite_index],
            rotation_difference
        )
        car_rect = rotated_car.get_rect(
            center=(round(x), round(y))
        )
        screen.blit(rotated_car, car_rect)

        # Visa väggens startpunkt medan den placeras.
        if wall_start is not None:
            pygame.draw.circle(
                screen,
                (255, 255, 0),
                (round(wall_start.x), round(wall_start.y)),
                5
            )

        pygame.display.flip()
        clock.tick(60)

finally:
    pygame.quit()
