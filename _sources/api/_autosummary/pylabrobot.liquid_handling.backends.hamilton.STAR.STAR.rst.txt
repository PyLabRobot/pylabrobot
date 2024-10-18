pylabrobot.liquid\_handling.backends.hamilton.STAR.STAR
=======================================================

.. currentmodule:: pylabrobot.liquid_handling.backends.hamilton.STAR

.. autoclass:: STAR

   
   
   .. rubric:: Attributes

   .. autosummary::
      :toctree: .
   
      ~STAR.core_parked
      ~STAR.deck
      ~STAR.extended_conf
      ~STAR.iswap_parked
      ~STAR.module_id_length
      ~STAR.num_channels
      ~STAR.unsafe
   
   

   
   
   .. rubric:: Methods

   .. autosummary::
      :toctree: .
   
      ~STAR.__init__
      ~STAR.additional_time_stamp
      ~STAR.aspirate
      ~STAR.aspirate96
      ~STAR.aspirate_core_96
      ~STAR.aspirate_pip
      ~STAR.assigned_resource_callback
      ~STAR.check_fw_string_error
      ~STAR.check_type_is_hhc
      ~STAR.check_type_is_hhs
      ~STAR.close_plate_lock
      ~STAR.collapse_gripper_arm
      ~STAR.configure_node_names
      ~STAR.core_check_resource_exists_at_location_center
      ~STAR.core_get_plate
      ~STAR.core_move_picked_up_resource
      ~STAR.core_move_plate_to_position
      ~STAR.core_open_gripper
      ~STAR.core_pick_up_resource
      ~STAR.core_put_plate
      ~STAR.core_release_picked_up_resource
      ~STAR.define_tip_needle
      ~STAR.deserialize
      ~STAR.disable_cover_control
      ~STAR.discard_tip
      ~STAR.discard_tips_core96
      ~STAR.dispense
      ~STAR.dispense96
      ~STAR.dispense_core_96
      ~STAR.dispense_pip
      ~STAR.drain_dual_chamber_system
      ~STAR.drop_tips
      ~STAR.drop_tips96
      ~STAR.enable_cover_control
      ~STAR.fill_selected_dual_chamber
      ~STAR.get_available_devices
      ~STAR.get_core
      ~STAR.get_id_from_fw_response
      ~STAR.get_logic_iswap_position
      ~STAR.get_or_assign_tip_type_index
      ~STAR.get_temperature_at_hhc
      ~STAR.get_temperature_at_hhs
      ~STAR.get_ttti
      ~STAR.halt
      ~STAR.initialize_auto_load
      ~STAR.initialize_autoload
      ~STAR.initialize_core_96_head
      ~STAR.initialize_dual_pump_station_valves
      ~STAR.initialize_hhc
      ~STAR.initialize_hhs
      ~STAR.initialize_iswap
      ~STAR.initialize_pipetting_channels
      ~STAR.iswap_close_gripper
      ~STAR.iswap_get_plate
      ~STAR.iswap_move_picked_up_resource
      ~STAR.iswap_open_gripper
      ~STAR.iswap_pick_up_resource
      ~STAR.iswap_put_plate
      ~STAR.iswap_release_picked_up_resource
      ~STAR.iswap_rotate
      ~STAR.list_available_devices
      ~STAR.load_carrier
      ~STAR.lock_cover
      ~STAR.move_all_channels_in_z_safety
      ~STAR.move_all_pipetting_channels_to_defined_position
      ~STAR.move_auto_load_to_z_save_position
      ~STAR.move_autoload_to_slot
      ~STAR.move_channel_x
      ~STAR.move_channel_y
      ~STAR.move_channel_z
      ~STAR.move_core_96_head_to_defined_position
      ~STAR.move_core_96_to_safe_position
      ~STAR.move_iswap_x_direction
      ~STAR.move_iswap_y_direction
      ~STAR.move_iswap_z_direction
      ~STAR.move_left_x_arm_to_position_with_all_attached_components_in_z_safety_position
      ~STAR.move_plate_to_position
      ~STAR.move_resource
      ~STAR.move_right_x_arm_to_position_with_all_attached_components_in_z_safety_position
      ~STAR.occupy_and_provide_area_for_external_access
      ~STAR.open_not_initialized_gripper
      ~STAR.open_plate_lock
      ~STAR.park_autoload
      ~STAR.park_iswap
      ~STAR.pick_up_tip
      ~STAR.pick_up_tips
      ~STAR.pick_up_tips96
      ~STAR.pick_up_tips_core96
      ~STAR.position_components_for_free_iswap_y_range
      ~STAR.position_left_x_arm_
      ~STAR.position_max_free_y_for_n
      ~STAR.position_right_x_arm_
      ~STAR.position_single_pipetting_channel_in_y_direction
      ~STAR.position_single_pipetting_channel_in_z_direction
      ~STAR.pre_initialize_instrument
      ~STAR.prepare_for_manual_channel_operation
      ~STAR.prepare_iswap_teaching
      ~STAR.probe_z_height_using_channel
      ~STAR.put_core
      ~STAR.query_whether_temperature_reached_at_hhc
      ~STAR.read
      ~STAR.release_all_occupied_areas
      ~STAR.release_occupied_area
      ~STAR.request_additional_timestamp_data
      ~STAR.request_auto_load_slot_position
      ~STAR.request_autoload_initialization_status
      ~STAR.request_core_96_head_channel_tadm_error_status
      ~STAR.request_core_96_head_channel_tadm_status
      ~STAR.request_core_96_head_initialization_status
      ~STAR.request_cover_open
      ~STAR.request_deck_data
      ~STAR.request_download_date
      ~STAR.request_eeprom_data_correctness
      ~STAR.request_electronic_board_type
      ~STAR.request_error_code
      ~STAR.request_extended_configuration
      ~STAR.request_firmware_version
      ~STAR.request_installation_data
      ~STAR.request_instrument_initialization_status
      ~STAR.request_iswap_in_parking_position
      ~STAR.request_iswap_initialization_status
      ~STAR.request_iswap_position
      ~STAR.request_left_x_arm_last_collision_type
      ~STAR.request_left_x_arm_position
      ~STAR.request_machine_configuration
      ~STAR.request_master_status
      ~STAR.request_maximal_ranges_of_x_drives
      ~STAR.request_name_of_last_faulty_parameter
      ~STAR.request_node_names
      ~STAR.request_number_of_presence_sensors_installed
      ~STAR.request_parameter_value
      ~STAR.request_pip_channel_validation_status
      ~STAR.request_pip_height_last_lld
      ~STAR.request_plate_in_iswap
      ~STAR.request_position_of_core_96_head
      ~STAR.request_present_wrap_size_of_installed_arms
      ~STAR.request_pump_settings
      ~STAR.request_right_x_arm_last_collision_type
      ~STAR.request_right_x_arm_position
      ~STAR.request_single_carrier_presence
      ~STAR.request_supply_voltage
      ~STAR.request_tadm_status
      ~STAR.request_technical_status_of_assemblies
      ~STAR.request_tip_presence
      ~STAR.request_tip_presence_in_core_96_head
      ~STAR.request_verification_data
      ~STAR.request_xl_channel_validation_status
      ~STAR.request_y_pos_channel_n
      ~STAR.request_z_pos_channel_n
      ~STAR.reset_output
      ~STAR.save_all_cycle_counters
      ~STAR.save_download_date
      ~STAR.save_pip_channel_validation_status
      ~STAR.save_technical_status_of_assemblies
      ~STAR.save_xl_channel_validation_status
      ~STAR.search_for_teach_in_signal_using_pipetting_channel_n_in_x_direction
      ~STAR.send_command
      ~STAR.send_raw_command
      ~STAR.serialize
      ~STAR.set_barcode_type
      ~STAR.set_carrier_monitoring
      ~STAR.set_cover_output
      ~STAR.set_deck
      ~STAR.set_deck_data
      ~STAR.set_instrument_configuration
      ~STAR.set_loading_indicators
      ~STAR.set_minimum_traversal_height
      ~STAR.set_not_stop
      ~STAR.set_single_step_mode
      ~STAR.set_x_offset_x_axis_core_96_head
      ~STAR.set_x_offset_x_axis_core_nano_pipettor_head
      ~STAR.set_x_offset_x_axis_iswap
      ~STAR.setup
      ~STAR.spread_pip_channels
      ~STAR.start_shaking_at_hhs
      ~STAR.start_temperature_control_at_hhc
      ~STAR.start_temperature_control_at_hhs
      ~STAR.stop
      ~STAR.stop_shaking_at_hhs
      ~STAR.stop_temperature_control_at_hhc
      ~STAR.stop_temperature_control_at_hhs
      ~STAR.store_installation_data
      ~STAR.store_verification_data
      ~STAR.trigger_next_step
      ~STAR.unassigned_resource_callback
      ~STAR.unload_carrier
      ~STAR.unlock_cover
      ~STAR.write
   
   