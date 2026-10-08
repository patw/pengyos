/*
 * pengyos-background-portal — org.freedesktop.impl.portal.Background
 *
 * Why this exists
 * ---------------
 * xdg-desktop-portal exports the Background portal only when some backend
 * declares it: init_background() (xdg-desktop-portal 1.22.1, src/background.c)
 * returns immediately when no .portal file names
 * org.freedesktop.impl.portal.Background. Neither COSMIC's backend nor
 * xdg-desktop-portal-gtk declares it, so the interface does not exist and
 * flatpak apps that ask to run in the background or to start at login
 * (SyncThingy, and anything else using RequestBackground) fail with
 *   GDBus.Error:org.freedesktop.DBus.Error.UnknownMethod:
 *   No such interface "org.freedesktop.portal.Background"
 * and report it as a denied permission.
 *
 * What is left for a backend
 * --------------------------
 * Everything that needs judgement is already done by the portal core, and this
 * file deliberately does not duplicate it:
 *   - the permission store (table "background") lookup and write,
 *   * the "Allow <app> to Run in the Background?" dialog, which the core runs
 *     through the Access portal (COSMIC's backend implements that),
 *   - writing ~/.config/autostart/<app-id>.desktop, with the Exec rewritten to
 *     `flatpak run <id> …` and X-Flatpak set.
 *
 * A backend owes three members, and each one touches a running app if it
 * answers carelessly:
 *
 *   GetAppState — per app: 0 background, 1 running, 2 active. The core KILLS an
 *     app it believes is in the background and lacks permission, so guessing
 *     wrong here is harmful, not merely inaccurate. We do not know COSMIC's
 *     window state (that needs the compositor), so every live flatpak instance
 *     is reported as RUNNING: the one direction that can never nag or kill.
 *     Real window state is the upgrade path, in cosmic-portal.
 *
 *   NotifyBackground — answer IGNORE (2), "allow this instance, change no
 *     permission". FORBID (0) makes the core SIGKILL the app and store a
 *     standing denial; ALLOW (1) stores a standing permission. Neither is this
 *     helper's call to make.
 *
 *   EnableAutostart — not implemented. 1.22 never calls it (the core writes the
 *     autostart file itself); a second writer of that file is how it gets
 *     corrupted.
 *
 * Build (see ../post-build.sh, which cross-compiles and installs this):
 *   $(CC) background-portal.c -o background-portal $(pkg-config --cflags --libs gio-2.0)
 */

#include <errno.h>
#include <gio/gio.h>
#include <glib.h>
#include <signal.h>
#include <stdlib.h>

#define PORTAL_BUS_NAME "org.freedesktop.impl.portal.desktop.pengyos"
#define PORTAL_OBJ_PATH "/org/freedesktop/portal/desktop"

#define APP_STATE_RUNNING 1u

/* The core treats this as "allow this instance, change no permission". */
#define NOTIFY_IGNORE 2u

static const char introspection_xml[] =
  "<node>"
  "  <interface name='org.freedesktop.impl.portal.Background'>"
  "    <method name='GetAppState'>"
  "      <arg type='a{sv}' name='apps' direction='out'/>"
  "    </method>"
  "    <method name='NotifyBackground'>"
  "      <arg type='o' name='handle' direction='in'/>"
  "      <arg type='s' name='app_id' direction='in'/>"
  "      <arg type='s' name='name' direction='in'/>"
  "      <arg type='u' name='response' direction='out'/>"
  "      <arg type='a{sv}' name='results' direction='out'/>"
  "    </method>"
  "    <signal name='RunningApplicationsChanged'/>"
  "  </interface>"
  "</node>";

/* Is the instance's pid still there? A missing pid file counts as alive:
 * flatpak does not always leave one, and "unknown" must fall on the side that
 * does not kill anything. */
static gboolean
instance_alive (const char *dir)
{
  g_autofree char *path = g_build_filename (dir, "pid", NULL);
  g_autofree char *contents = NULL;

  if (!g_file_get_contents (path, &contents, NULL, NULL))
    return TRUE;

  long pid = g_ascii_strtoll (g_strstrip (contents), NULL, 10);
  if (pid <= 0)
    return TRUE;

  if (kill ((pid_t) pid, 0) == 0)
    return TRUE;

  /* ESRCH means the instance is gone; EPERM means it is someone else's and
   * very much alive. */
  return errno == EPERM;
}

/* App id of a flatpak instance directory, from its info keyfile — the same
 * source the portal core reads ($XDG_RUNTIME_DIR/.flatpak/<id>/info). */
static char *
instance_app_id (const char *dir)
{
  g_autofree char *path = g_build_filename (dir, "info", NULL);
  g_autoptr(GKeyFile) key_file = g_key_file_new ();

  if (!g_key_file_load_from_file (key_file, path, G_KEY_FILE_NONE, NULL))
    return NULL;

  return g_key_file_get_string (key_file, "Application", "name", NULL);
}

/* Every live flatpak instance, reported as RUNNING. */
static void
build_app_states (GVariantBuilder *builder)
{
  const char *runtime = g_get_user_runtime_dir ();
  g_autofree char *base = g_build_filename (runtime, ".flatpak", NULL);
  g_autoptr(GDir) dir = g_dir_open (base, 0, NULL);

  if (dir == NULL)
    return;

  const char *entry;
  while ((entry = g_dir_read_name (dir)) != NULL)
    {
      g_autofree char *instance_dir = g_build_filename (base, entry, NULL);
      g_autofree char *app_id = NULL;

      if (!g_file_test (instance_dir, G_FILE_TEST_IS_DIR))
        continue;

      if (!instance_alive (instance_dir))
        continue;

      app_id = instance_app_id (instance_dir);
      if (app_id == NULL)
        continue;

      g_variant_builder_add (builder, "{sv}", app_id,
                             g_variant_new_uint32 (APP_STATE_RUNNING));
    }
}

static void
handle_method_call (GDBusConnection       *connection,
                    const char            *sender,
                    const char            *object_path,
                    const char            *interface_name,
                    const char            *method_name,
                    GVariant              *parameters,
                    GDBusMethodInvocation *invocation,
                    gpointer               user_data)
{
  (void) connection;
  (void) sender;
  (void) object_path;
  (void) interface_name;
  (void) user_data;

  if (g_str_equal (method_name, "GetAppState"))
    {
      GVariantBuilder apps;
      g_variant_builder_init (&apps, G_VARIANT_TYPE ("a{sv}"));
      build_app_states (&apps);

      g_dbus_method_invocation_return_value (invocation,
          g_variant_new ("(a{sv})", &apps));
      return;
    }

  if (g_str_equal (method_name, "NotifyBackground"))
    {
      /* Read the arguments (owned by the invocation, not by us) so the call is
       * well-formed, then answer IGNORE with an empty results dict. */
      if (parameters != NULL)
        {
          const char *handle, *app_id, *name;
          g_variant_get (parameters, "(&o&s&s)", &handle, &app_id, &name);
        }

      GVariantBuilder results;
      g_variant_builder_init (&results, G_VARIANT_TYPE ("a{sv}"));

      g_dbus_method_invocation_return_value (invocation,
          g_variant_new ("(ua{sv})", NOTIFY_IGNORE, &results));
      return;
    }

  g_dbus_method_invocation_return_error (invocation, G_DBUS_ERROR,
                                         G_DBUS_ERROR_UNKNOWN_METHOD,
                                         "background-portal does not implement %s",
                                         method_name);
}

static void
on_bus_acquired (GDBusConnection *connection, const char *name, gpointer user_data)
{
  GMainLoop *loop = user_data;
  g_autoptr(GError) error = NULL;
  g_autoptr(GDBusNodeInfo) node = g_dbus_node_info_new_for_xml (introspection_xml,
                                                                &error);

  if (node == NULL)
    {
      g_warning ("Could not parse the interface definition: %s", error->message);
      g_main_loop_quit (loop);
      return;
    }

  /* register_object (not the removed register_interface): a vtable over one
   * object path, which is exactly the shape the portal core expects. */
  static const GDBusInterfaceVTable vtable = { handle_method_call, NULL, NULL, {0} };

  if (g_dbus_connection_register_object (connection, PORTAL_OBJ_PATH,
                                         node->interfaces[0], &vtable,
                                         loop, NULL, &error) == 0)
    {
      g_warning ("Could not export %s: %s", PORTAL_BUS_NAME, error->message);
      g_main_loop_quit (loop);
      return;
    }

  (void) name;
}

static void
on_name_lost (GDBusConnection *connection, const char *name, gpointer user_data)
{
  (void) connection;
  (void) name;
  /* The bus went away, or something else already owns the name. Either way
   * there is nothing for us to do; D-Bus activation starts us again. */
  g_main_loop_quit ((GMainLoop *) user_data);
}

int
main (int argc, char **argv)
{
  (void) argc;
  (void) argv;

  GMainLoop *loop = g_main_loop_new (NULL, FALSE);

  g_bus_own_name (G_BUS_TYPE_SESSION, PORTAL_BUS_NAME, G_BUS_NAME_OWNER_FLAGS_NONE,
                  on_bus_acquired, NULL, on_name_lost, loop, NULL);

  g_main_loop_run (loop);

  g_main_loop_unref (loop);
  return 0;
}
