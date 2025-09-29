# Notas de Base de Datos
Este proyecto usa MySQL/MariaDB. Importa tu dump:
  mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS repaircell_db DEFAULT CHARSET=utf8mb4;"
  mysql -u root -p repaircell_db < "repaircell_db.sql"
