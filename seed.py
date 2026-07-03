import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.database import AsyncSessionLocal
from app.models import (
    CategoriaTaller,
    Cliente,
    DisputaSolicitud,
    EstadoSolicitud,
    EvidenciaSolicitud,
    HistorialEvento,
    Notificacion,
    Operador,
    PagoSolicitud,
    Role,
    Solicitud,
    Taller,
    Tecnico,
    TipoIncidente,
    User,
    Vehiculo,
)
from app.models.enums import EstadoSolicitudEnum, NombreRol, PrioridadSolicitud
from app.utils.auth import hash_password


INCIDENTES = [
    ("Llanta ponchada", "Sustitución o reparación inmediata de llanta"),
    ("Sin combustible", "Vehículo detenido por falta de combustible"),
    ("Falla mecánica", "Avería mecánica general"),
    ("Accidente", "Incidente con daño físico o material"),
    ("Bloqueo de tráfico", "Vehículo inmovilizado en zona crítica"),
]


async def reset_tables() -> None:
    async with AsyncSessionLocal() as session:
        for model in [
            DisputaSolicitud,
            PagoSolicitud,
            EvidenciaSolicitud,
            HistorialEvento,
            Notificacion,
            Solicitud,
            Vehiculo,
            Taller,
            CategoriaTaller,
            Tecnico,
            Operador,
            Cliente,
            User,
            Role,
            TipoIncidente,
            EstadoSolicitud,
        ]:
            await session.execute(delete(model))
        await session.commit()


async def seed() -> None:
    await reset_tables()

    async with AsyncSessionLocal() as session:
        roles = {name.value: Role(name=name.value) for name in NombreRol}
        estados = {name.value: EstadoSolicitud(nombre=name.value) for name in EstadoSolicitudEnum}
        tipos = {nombre: TipoIncidente(nombre=nombre, descripcion=descripcion) for nombre, descripcion in INCIDENTES}

        categorias = {
            "chaperia_pintura": CategoriaTaller(slug="chaperia_pintura", nombre="Chapería y pintura"),
            "llantas": CategoriaTaller(slug="llantas", nombre="Diagnóstico y reparación de llantas"),
            "motor": CategoriaTaller(slug="motor", nombre="Reparación de motores"),
            "electricidad": CategoriaTaller(slug="electricidad", nombre="Sistemas eléctricos"),
            "suspension": CategoriaTaller(slug="suspension", nombre="Suspensiones"),
            "general": CategoriaTaller(slug="general", nombre="Servicios generales"),
        }

        session.add_all([*roles.values(), *estados.values(), *tipos.values(), *categorias.values()])
        await session.flush()

        usuarios: list[User] = []
        clientes: list[Cliente] = []
        tecnicos: list[Tecnico] = []

        # Usuarios base por rol.
        for email, rol in [
            ("admin@emergency.com", NombreRol.ADMINISTRADOR.value),
            ("operador@emergency.com", NombreRol.OPERADOR.value),
            ("tecnico@emergency.com", NombreRol.TECNICO.value),
            ("cliente@emergency.com", NombreRol.CLIENTE.value),
            ("taller@emergency.com", NombreRol.TALLER.value),
        ]:
            user = User(email=email, password_hash=hash_password("Password123*"))
            user.roles.append(roles[rol])
            usuarios.append(user)
            session.add(user)

        for index in range(1, 6):
            user = User(email=f"cliente{index}@emergency.com", password_hash=hash_password("Password123*"))
            user.roles.append(roles[NombreRol.CLIENTE.value])
            usuarios.append(user)
            session.add(user)

        for index in range(1, 3):
            user = User(email=f"operador{index}@emergency.com", password_hash=hash_password("Password123*"))
            user.roles.append(roles[NombreRol.OPERADOR.value])
            usuarios.append(user)
            session.add(user)

        for index in range(1, 4):
            user = User(email=f"tecnico{index}@emergency.com", password_hash=hash_password("Password123*"))
            user.roles.append(roles[NombreRol.TECNICO.value])
            usuarios.append(user)
            session.add(user)

        for index in range(1, 3):
            user = User(email=f"taller{index}@emergency.com", password_hash=hash_password("Password123*"))
            user.roles.append(roles[NombreRol.TALLER.value])
            usuarios.append(user)
            session.add(user)

        await session.flush()

        for index, user in enumerate([u for u in usuarios if any(role.name == "CLIENTE" for role in u.roles)], start=1):
            cliente = Cliente(
                user_id=user.id,
                nombre=f"Cliente {index}",
                telefono=f"55123456{index:02d}",
                direccion=f"Dirección cliente {index}, Ciudad de México",
                latitud=-17.7833 + (index * 0.004),
                longitud=-63.1821 + (index * 0.004),
            )
            clientes.append(cliente)
            session.add(cliente)

        tecnicos_data = [
            ("Técnico Norte", -17.7446, -63.1678, "Mecánica rápida"),
            ("Técnico Centro", -17.7833, -63.1821, "Grúa y batería"),
            ("Técnico Sur", -17.8279, -63.1862, "Diagnóstico electrónico"),
            ("Técnico Extra", -17.8024, -63.1467, "Asistencia general"),
        ]
        tecnicos_users = [u for u in usuarios if any(role.name == "TECNICO" for role in u.roles)]
        for user, (nombre, lat, lon, especialidad) in zip(tecnicos_users, tecnicos_data, strict=False):
            tecnico = Tecnico(
                user_id=user.id,
                nombre=nombre,
                telefono="5599990000",
                especialidad=especialidad,
                latitud_actual=lat,
                longitud_actual=lon,
                disponibilidad=True,
            )
            tecnicos.append(tecnico)
            session.add(tecnico)

        operadores_users = [u for u in usuarios if any(role.name == "OPERADOR" for role in u.roles)]
        for index, user in enumerate(operadores_users, start=1):
            session.add(Operador(user_id=user.id, nombre=f"Operador {index}", turno="Mixto"))

        talleres_users = [u for u in usuarios if any(role.name == "TALLER" for role in u.roles)]
        talleres = [
            Taller(
                user_id=talleres_users[0].id if len(talleres_users) > 0 else None,
                categoria_id=categorias["electricidad"].id,
                nombre="Taller Centro",
                direccion="Av. Central 100",
                latitud=-17.7836,
                longitud=-63.1822,
                telefono="5511111111",
                horarios="Lun-Sáb 08:00-18:00",
                certificaciones="ISO 9001",
                tarifas_base={"electricidad": 850.0, "general": 550.0},
                descuentos_marca={"TOYOTA": 15.0},
                rating_promedio=4.6,
                rating_total=128,
                capacidad=8,
                servicios="grúa|batería|diagnóstico",
                disponible=True,
                acepta_automaticamente=False,
            ),
            Taller(
                user_id=talleres_users[1].id if len(talleres_users) > 1 else None,
                categoria_id=categorias["motor"].id,
                nombre="Taller Norte",
                direccion="Av. Norte 200",
                latitud=-17.7487,
                longitud=-63.1698,
                telefono="5522222222",
                horarios="Lun-Vie 08:30-17:30",
                certificaciones="ASE",
                tarifas_base={"motor": 2200.0, "general": 650.0},
                rating_promedio=4.2,
                rating_total=74,
                capacidad=6,
                servicios="mecánica|combustible",
                disponible=True,
                acepta_automaticamente=True,
            ),
            Taller(
                categoria_id=categorias["llantas"].id,
                nombre="Taller Sur",
                direccion="Av. Sur 300",
                latitud=-17.821,
                longitud=-63.1881,
                telefono="5533333333",
                horarios="Lun-Dom 09:00-20:00",
                certificaciones="",
                tarifas_base={"llantas": 320.0, "general": 500.0},
                rating_promedio=4.7,
                rating_total=203,
                capacidad=5,
                servicios="llantas|grúa",
                disponible=True,
                acepta_automaticamente=False,
            ),
            Taller(
                categoria_id=categorias["chaperia_pintura"].id,
                nombre="Taller Chapería Express",
                direccion="Av. Banzer 123",
                latitud=-17.7602,
                longitud=-63.1835,
                telefono="5544444444",
                horarios="Lun-Sáb 08:00-19:00",
                certificaciones="PPG Certified",
                tarifas_base={"chaperia_pintura": 1800.0, "general": 700.0},
                rating_promedio=4.4,
                rating_total=61,
                capacidad=7,
                servicios="chapería|pintura|pulido",
                disponible=True,
                acepta_automaticamente=False,
            ),
            Taller(
                categoria_id=categorias["suspension"].id,
                nombre="Taller Suspensión Pro",
                direccion="Av. Doble Vía La Guardia 456",
                latitud=-17.8058,
                longitud=-63.2029,
                telefono="5555555555",
                horarios="Lun-Sáb 08:00-18:30",
                certificaciones="",
                tarifas_base={"suspension": 1200.0, "general": 600.0},
                rating_promedio=4.1,
                rating_total=39,
                capacidad=4,
                servicios="suspensión|alineación|balanceo",
                disponible=True,
                acepta_automaticamente=False,
            ),
        ]
        session.add_all(talleres)
        await session.flush()

        for index, tecnico in enumerate(tecnicos):
            tecnico.taller_id = talleres[index % len(talleres)].id

        vehiculos: list[Vehiculo] = []
        for index in range(10):
            cliente = clientes[index % len(clientes)]
            vehiculo = Vehiculo(
                cliente_id=cliente.id,
                marca="Nissan" if index % 2 == 0 else "Chevrolet",
                modelo=f"Modelo {index + 1}",
                anio=2015 + (index % 8),
                placa=f"EMG{100 + index}",
                color="Rojo" if index % 2 == 0 else "Azul",
                tipo_combustible="Gasolina",
            )
            vehiculos.append(vehiculo)
            session.add(vehiculo)

        await session.flush()

        estado_values = list(estados.values())
        tipo_values = list(tipos.values())
        for index in range(20):
            cliente = clientes[index % len(clientes)]
            vehiculo = vehiculos[index % len(vehiculos)]
            estado = estado_values[index % len(estado_values)]
            tecnico = tecnicos[index % len(tecnicos)] if index % 3 != 0 else None
            taller = talleres[index % len(talleres)]
            solicitud = Solicitud(
                cliente_id=cliente.id,
                vehiculo_id=vehiculo.id,
                tecnico_id=tecnico.id if tecnico else None,
                taller_id=taller.id,
                tipo_incidente_id=tipo_values[index % len(tipo_values)].id,
                estado_id=estado.id,
                latitud_incidente=cliente.latitud or -17.7833,
                longitud_incidente=cliente.longitud or -63.1821,
                descripcion=f"Solicitud de prueba #{index + 1}",
                foto_url=f"https://picsum.photos/seed/{index}/400/300",
                clasificacion_confianza=0.55 if index % 4 == 0 else 0.82,
                requiere_revision_manual=index % 4 == 0,
                motivo_prioridad="Seed de priorización",
                resumen_ia="Clasificación generada para dataset de prueba",
                prioridad=list(PrioridadSolicitud)[index % len(PrioridadSolicitud)],
                fecha_solicitud=datetime.now(timezone.utc) - timedelta(hours=index),
                fecha_asignacion=datetime.now(timezone.utc) - timedelta(hours=index - 1) if tecnico else None,
                fecha_atencion=datetime.now(timezone.utc) - timedelta(minutes=30) if estado.nombre in {"EN_ATENCION", "COMPLETADA"} else None,
                fecha_cierre=datetime.now(timezone.utc) if estado.nombre == "COMPLETADA" else None,
            )
            session.add(solicitud)
            await session.flush()

            session.add(
                HistorialEvento(
                    solicitud_id=solicitud.id,
                    estado_anterior="NUEVA",
                    estado_nuevo=estado.nombre,
                    observacion="Evento generado en seed",
                    usuario_id=cliente.user_id,
                )
            )
            session.add(
                Notificacion(
                    usuario_id=cliente.user_id,
                    titulo="Solicitud de prueba",
                    mensaje=f"Seguimiento para solicitud #{solicitud.id}",
                    tipo="SEED",
                    leida=index % 2 == 0,
                )
            )
            if estado.nombre == "COMPLETADA":
                session.add(
                    PagoSolicitud(
                        solicitud_id=solicitud.id,
                        cliente_id=cliente.id,
                        taller_id=taller.id,
                        monto_total=350.0 + index,
                        monto_comision=35.0 + (index * 0.1),
                        monto_taller=315.0 + (index * 0.9),
                        metodo_pago="tarjeta",
                        estado="PAGADO",
                        fecha_pago=datetime.now(timezone.utc),
                    )
                )
            if index % 5 == 0:
                session.add(
                    EvidenciaSolicitud(
                        solicitud_id=solicitud.id,
                        usuario_id=cliente.user_id,
                        tipo="TEXT",
                        contenido_texto="Evidencia textual de prueba",
                    )
                )
            if index % 7 == 0:
                session.add(
                    DisputaSolicitud(
                        solicitud_id=solicitud.id,
                        usuario_id=cliente.user_id,
                        motivo="Cobro",
                        detalle="Revisión de cobro de prueba",
                        estado="ABIERTA",
                    )
                )

        await session.commit()

        admin_user = await session.scalar(select(User).where(User.email == "admin@emergency.com"))
        if admin_user:
            print(f"Seed completado. Usuario administrador: {admin_user.email} / Password123*")

    # ── Super-admin del control plane ──────────────────────────────
    # El super-admin NO vive dentro de un tenant — está en la control DB.
    # Lo creamos acá automáticamente para que la demo funcione out-of-
    # the-box, pero en producción debería crearse con:
    #     python -m app.control_plane.bootstrap --email X --password Y
    try:
        from app.control_plane.bootstrap import bootstrap_super_admin
        _, created = await bootstrap_super_admin(
            email="superadmin@platform.com",
            password="SuperSecret123*",
            display_name="Super Admin (demo)",
        )
        msg = "creado" if created else "ya existía"
        print(f"Super-admin del control plane {msg}: superadmin@platform.com / SuperSecret123*")
    except Exception as exc:
        print(f"AVISO: no se pudo bootstrap super-admin ({type(exc).__name__}). Ejecuta `python -m app.control_plane.bootstrap` manualmente.")


if __name__ == "__main__":
    asyncio.run(seed())
